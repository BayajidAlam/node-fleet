"""
EC2 manager for scaling worker nodes
Handles instance launch and termination with Multi-AZ support and spot instances
"""

import logging
import boto3
import subprocess
import os
import time
import tempfile
from typing import Dict, List
from datetime import datetime, timezone
from collections import defaultdict
from botocore.exceptions import ClientError
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from multi_az_helper import select_subnet_for_new_instance, get_az_distribution
from spot_instance_helper import (
    calculate_spot_ondemand_mix,
    get_spot_interruption_notices,
    handle_spot_interruption,
    should_use_spot_instance
)

logger = logging.getLogger()


class EC2Manager:
    """Manages EC2 worker node scaling operations with Multi-AZ distribution"""
    
    def __init__(self, worker_template_id: str, worker_spot_template_id: str, spot_percentage: int = 70):
        self.worker_template_id = worker_template_id
        self.worker_spot_template_id = worker_spot_template_id
        self.spot_percentage = spot_percentage
        self.ec2_client = boto3.client('ec2')
        # Multi-AZ: Subnet IDs for ap-southeast-1a and ap-southeast-1b
        self.available_subnets = self._get_cluster_subnets()
    
    def handle_spot_interruption_event(self, instance_id: str) -> Dict:
        """
        Handle Spot Instance Interruption Warning event
        1. Tag instance as interrupted
        2. Drain node immediately
        
        Args:
            instance_id: EC2 Instance ID from event
            
        Returns:
            Result dict
        """
        logger.info(f"Handling Spot Interruption for {instance_id}")
        
        # 1. Tag instance (using helper)
        try:
            handle_spot_interruption(instance_id, self._get_cluster_id())
        except Exception as e:
            logger.error(f"Failed to tag spot interruption: {e}")
            
        # 2. Resolve Node Name
        node_name = self._get_node_name_from_instance(instance_id)
        if not node_name:
            logger.warning(f"Could not resolve node name for {instance_id}. Skipping drain.")
            return {"success": False, "reason": "Node name not found"}
            
        # 3. Drain Node
        logger.info(f"Draining node {node_name} due to spot interruption")
        drain_result = self._drain_node(node_name, timeout=300) # 5 min max (consistent with scale-down)
        
        if drain_result:
            logger.info(f"Successfully drained {node_name}. Termination will happen by AWS.")
            return {"success": True, "action": "drained", "node": node_name}
        else:
            logger.error(f"Failed to drain {node_name}")
            return {"success": False, "reason": "Drain failed"}

    def scale_up(self, nodes_to_add: int, reason: str) -> Dict:
        """
        Launch new worker nodes (mix of on-demand and spot)
        
        Args:
            nodes_to_add: Number of nodes to add
            reason: Reason for scaling up
        
        Returns:
            Dictionary with instance_ids and details
        """
        logger.info(f"Scaling up: Adding {nodes_to_add} nodes. Reason: {reason}")
        
        # Check for spot interruptions first
        interrupted_instances = get_spot_interruption_notices(self._get_cluster_id())
        if interrupted_instances:
            logger.warning(f"Handling {len(interrupted_instances)} spot interruptions")
            for instance_id in interrupted_instances:
                handle_spot_interruption(instance_id, self._get_cluster_id())
        
        # Get current instance counts
        worker_instances = self._get_worker_instances()
        current_spot_count = sum(1 for inst in worker_instances if inst.get('InstanceLifecycle') == 'spot')
        current_ondemand_count = len(worker_instances) - current_spot_count
        current_total = len(worker_instances)
        
        # Calculate smart spot/on-demand mix to maintain target ratio
        mix = calculate_spot_ondemand_mix(
            current_nodes=current_total,
            desired_nodes=current_total + nodes_to_add,
            existing_spot_count=current_spot_count,
            existing_ondemand_count=current_ondemand_count,
            target_spot_ratio=self.spot_percentage / 100.0
        )
        
        spot_count = mix['spot']
        ondemand_count = mix['ondemand']
        
        instance_ids = []
        
        try:
            # Launch spot instances
            if spot_count > 0:
                try:
                    logger.info(f"Launching {spot_count} spot instances")
                    spot_instances = self._launch_instances(
                        template_id=self.worker_spot_template_id,
                        count=spot_count,
                        instance_type="spot"
                    )
                    instance_ids.extend(spot_instances)
                except Exception as e:
                    logger.warning(f"Failed to launch Spot instances: {e}. Falling back to On-Demand.")
                    ondemand_count += spot_count
                    spot_count = 0
            
            # Launch on-demand instances
            if ondemand_count > 0:
                logger.info(f"Launching {ondemand_count} on-demand instances")
                ondemand_instances = self._launch_instances(
                    template_id=self.worker_template_id,
                    count=ondemand_count,
                    instance_type="on-demand"
                )
                instance_ids.extend(ondemand_instances)
            
            logger.info(f"Successfully launched {len(instance_ids)} instances: {instance_ids}")
            logger.info(f"New cluster composition: {current_spot_count + spot_count} spot + {current_ondemand_count + ondemand_count} on-demand")
            
            # Wait for nodes to join the cluster and become ready
            join_start_time = time.time()
            ready_nodes = self._wait_for_nodes_ready(instance_ids, timeout=300)
            join_latency_ms = int((time.time() - join_start_time) * 1000)
            
            logger.info(f"Node join latency: {join_latency_ms}ms, Ready nodes: {len(ready_nodes)}/{len(instance_ids)}")
            
            return {
                "success": True,
                "instance_ids": instance_ids,
                "spot_count": spot_count,
                "ondemand_count": ondemand_count,
                "ready_nodes": ready_nodes,
                "node_join_latency_ms": join_latency_ms
            }
            
        except Exception as e:
            logger.error(f"Failed to scale up: {str(e)}")
            raise
    
    def scale_down(self, nodes_to_remove: int, reason: str, state_manager=None) -> Dict:
        """
        Safely remove worker nodes.
        Drain is initiated asynchronously via SSM (non-blocking).
        Actual termination happens in the next Lambda invocation via complete_pending_drains().
        """
        logger.info(f"Scaling down: Removing {nodes_to_remove} nodes. Reason: {reason}")

        try:
            worker_instances = self._get_worker_instances()

            if len(worker_instances) <= nodes_to_remove:
                logger.warning(f"Not enough workers to remove. Current: {len(worker_instances)}, Requested: {nodes_to_remove}")
                nodes_to_remove = max(0, len(worker_instances) - 1)

            if nodes_to_remove == 0:
                return {"success": True, "instance_ids": [], "message": "No nodes removed"}

            instances_to_terminate = self._select_instances_for_termination(
                worker_instances,
                nodes_to_remove
            )

            master_instance_id = self._get_master_instance_id()
            draining = []

            for instance in instances_to_terminate:
                instance_id = instance['InstanceId']
                node_name = self._get_node_name_from_instance(instance_id)

                if node_name:
                    command_id = self._drain_node(node_name, instance_id)
                    if command_id:
                        drain_entry = {
                            'instance_id': instance_id,
                            'node_name': node_name,
                            'command_id': command_id,
                            'master_instance_id': master_instance_id,
                            'start_time': int(time.time()),
                        }
                        draining.append(drain_entry)
                        logger.info(f"Drain initiated for {node_name} (SSM: {command_id}). "
                                    f"Termination will complete in next Lambda invocation.")
                    else:
                        logger.warning(f"Failed to initiate drain for {node_name}, skipping termination")
                else:
                    # No node name (not yet joined) — terminate immediately
                    logger.info(f"No K8s node name for {instance_id}, terminating directly")
                    self.ec2_client.terminate_instances(InstanceIds=[instance_id])

            # Store drain state so next Lambda invocation can complete termination
            if draining and state_manager:
                state_manager.store_drain_state(draining)

            return {
                "success": True,
                "instance_ids": [d['instance_id'] for d in draining],
                "draining": draining,
                "message": f"Drain initiated for {len(draining)} node(s). Termination pending next invocation."
            }

        except Exception as e:
            logger.error(f"Failed to scale down: {str(e)}")
            raise
    
    def _get_cluster_subnets(self) -> List[str]:
        """Get public subnet IDs for the cluster (Multi-AZ)"""
        try:
            response = self.ec2_client.describe_subnets(
                Filters=[
                    {'Name': 'tag:Project', 'Values': ['node-fleet']},
                    {'Name': 'tag:Type', 'Values': ['public']}
                ]
            )
            return [subnet['SubnetId'] for subnet in response['Subnets']]
        except Exception as e:
            logger.error(f"Error fetching subnets: {e}")
            return []
    
    def _launch_instances(self, template_id: str, count: int, instance_type: str) -> List[str]:
        """Launch EC2 instances from launch template with Multi-AZ distribution"""
        try:
            # Get existing instances for AZ balancing
            existing_instances = self._get_worker_instances()
            current_distribution = get_az_distribution(existing_instances)
            logger.info(f"Current AZ distribution: {current_distribution}")
            
            instance_ids = []
            
            # Launch instances one at a time to ensure even AZ distribution
            for i in range(count):
                # Select subnet with fewest instances
                subnet_id = select_subnet_for_new_instance(existing_instances, self.available_subnets)
                
                response = self.ec2_client.run_instances(
                    LaunchTemplate={'LaunchTemplateId': template_id},
                    MinCount=1,
                    MaxCount=1,
                    SubnetId=subnet_id  # Multi-AZ: Distribute across subnets
                )
                
                launched_ids = [inst['InstanceId'] for inst in response['Instances']]
                instance_ids.extend(launched_ids)
                
                # Update existing instances list for next iteration
                existing_instances.extend(response['Instances'])
            
            # Tag all launched instances
            if instance_ids:
                self.ec2_client.create_tags(
                    Resources=instance_ids,
                    Tags=[
                        {'Key': 'InstanceType', 'Value': instance_type},
                        {'Key': 'LaunchedBy', 'Value': 'autoscaler'}
                    ]
                )
            
            return instance_ids
            
        except ClientError as e:
            logger.error(f"Error launching instances: {str(e)}")
            raise
    
    def _get_worker_instances(self) -> List[Dict]:
        """Get all running worker instances"""
        try:
            response = self.ec2_client.describe_instances(
                Filters=[
                    {'Name': 'tag:Role', 'Values': ['k3s-worker']},
                    {'Name': 'instance-state-name', 'Values': ['running']}
                ]
            )
            
            instances = []
            for reservation in response['Reservations']:
                instances.extend(reservation['Instances'])
            
            return instances
            
        except ClientError as e:
            logger.error(f"Error getting worker instances: {str(e)}")
            raise
    
    def _select_instances_for_termination(self, instances: List[Dict], count: int) -> List[Dict]:
        """
        Select instances for termination with pod-aware selection
        Priority: 
        1. Spot instances without StatefulSets (sorted by pod count)
        2. On-demand instances without StatefulSets (sorted by pod count)
        3. Spot instances with StatefulSets (fallback)
        4. On-demand instances with StatefulSets (fallback)
        """
        logger.info("Selecting instances for termination using enhanced pod-aware strategy")
        
        # Get pod info for all nodes
        node_info = self._get_node_pod_info()
        logger.info(f"Node pod distribution: {node_info}")
        
        def get_sort_key(instance):
            node_name = self._get_node_name_from_instance(instance['InstanceId'])
            info = node_info.get(node_name, {'count': 0, 'has_sts': False})
            
            # Weighted sort key:
            # Critical Protection: Critical (10000) or Single Replica (5000) heavily protected
            # StatefulSet: Has STS (1000) protected
            # Lifecycle: On-demand (100) protected over Spot (0)
            # Pod Count: More pods (1-99) protected
            # LaunchTime: Older (0-1) protected over Newest (LIFO - smaller weight)
            
            # Requirements: NEVER terminate nodes hosting System-critical, StatefulSet (unless safe), or Single-replica.
            # We give them massive weights so they are only picked as a last resort, or we can filter them.
            
            critical_weight = 10000 if info.get('has_critical') else 0
            single_replica_weight = 5000 if info.get('is_single_replica') else 0
            sts_weight = 1000 if info.get('has_sts') else 0
            lifecycle_weight = 100 if instance.get('InstanceLifecycle') != 'spot' else 0
            pod_weight = info.get('count', 0)
            
            # Newest (LIFO) should have LOWEST weight to be picked first.
            launch_timestamp = instance.get('LaunchTime', datetime.now(timezone.utc)).timestamp()
            lifo_weight = -launch_timestamp / 1000000  # Smaller is newer
            
            total_weight = critical_weight + single_replica_weight + sts_weight + lifecycle_weight + pod_weight + lifo_weight
            return total_weight

        instances_sorted = sorted(instances, key=get_sort_key)
        selected = instances_sorted[:count]
        
        for instance in selected:
            node_name = self._get_node_name_from_instance(instance['InstanceId'])
            info = node_info.get(node_name, {'count': 0, 'has_sts': False})
            logger.info(f"Selected {instance['InstanceId']} (Pods: {info['count']}, HasSTS: {info['has_sts']})")
            
        return selected

    
    def _get_node_pod_info(self) -> Dict[str, Dict]:
        """
        Get pod count and StatefulSet presence per node utilizing SSH for reliability
        
        Returns:
            Dict mapping node name to {'count': int, 'has_sts': bool, 'has_critical': bool, 'is_single_replica': bool}
        """
        try:
            # Execute kubectl get pods -A -o json via SSH
            cmd = "sudo k3s kubectl get pods -A -o json"
            import paramiko
            import io
            import json
            
            # Re-implementing simplified SSH exec here to capture output directly
            # or we could reuse _execute_master_command if we modified it to return output
            # For brevity and safety, let's implement the specific logic here or modify _execute
            
            # Reuse _get_ssh_key
            private_key_str = self._get_ssh_key()
            if not private_key_str:
                logger.error("No SSH key available for pod info")
                return {}
                
            private_key = paramiko.RSAKey.from_private_key(io.StringIO(private_key_str))
            
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            master_ip = self._get_master_ip()
            client.connect(hostname=master_ip, username="ubuntu", pkey=private_key, timeout=10)
            
            stdin, stdout, stderr = client.exec_command(cmd)
            exit_status = stdout.channel.recv_exit_status()
            out_str = stdout.read().decode().strip()
            client.close()
            
            if exit_status != 0:
                logger.error(f"Failed to get pods via SSH: {stderr.read().decode()}")
                return {}
                
            try:
                pod_list = json.loads(out_str)
            except json.JSONDecodeError:
                logger.error("Failed to decode JSON from pod list")
                return {}
            
            node_info = defaultdict(lambda: {'count': 0, 'has_sts': False, 'has_critical': False, 'is_single_replica': False})
            
            # We also need ReplicaSet info for single-replica check
            # Executing a second command to get ReplicaSets
            # Optimized: We'll skip the single-replica deep check via SSH for now or do a second call?
            # Let's do a second call for ReplicaSets to be thorough
            
            # Second SSH call for ReplicaSets - create a fresh client
            rs_client = paramiko.SSHClient()
            rs_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            rs_client.connect(hostname=self._get_master_ip(), username="ubuntu", pkey=private_key, timeout=10)
            rs_cmd = "sudo k3s kubectl get replicasets -A -o json"
            stdin, stdout, stderr = rs_client.exec_command(rs_cmd)
            rs_out = stdout.read().decode().strip()
            rs_client.close()
            
            rs_map = {} # namespace/name -> replicas
            try:
                rs_list = json.loads(rs_out)
                for rs in rs_list.get('items', []):
                    key = f"{rs['metadata']['namespace']}/{rs['metadata']['name']}"
                    rs_map[key] = rs.get('spec', {}).get('replicas', 1)
            except Exception as e:
                logger.warning(f"Failed to parse ReplicaSets via SSH: {e}. Assuming safe defaults.")
            
            for pod in pod_list.get('items', []):
                status = pod.get('status', {})
                if status.get('phase') in ['Succeeded', 'Failed']:
                    continue
                
                spec = pod.get('spec', {})
                node_name = spec.get('nodeName')
                if not node_name:
                    continue
                
                metadata = pod.get('metadata', {})
                namespace = metadata.get('namespace', 'default')
                
                # 1. Critical Pods
                if namespace == 'kube-system':
                    is_daemonset = False
                    owners = metadata.get('ownerReferences', [])
                    for owner in owners:
                        if owner.get('kind') == 'DaemonSet':
                            is_daemonset = True
                            break
                    
                    annotations = metadata.get('annotations', {})
                    is_static = 'kubernetes.io/config.mirror' in annotations
                    
                    if not is_daemonset and not is_static:
                        node_info[node_name]['has_critical'] = True
                    continue
                
                node_info[node_name]['count'] += 1
                
                # 2. StatefulSets & 3. Single Replica
                owners = metadata.get('ownerReferences', [])
                for owner in owners:
                    kind = owner.get('kind')
                    if kind == 'StatefulSet':
                        node_info[node_name]['has_sts'] = True
                    
                    elif kind == 'ReplicaSet':
                        rs_name = owner.get('name')
                        key = f"{namespace}/{rs_name}"
                        if rs_map.get(key) == 1:
                            node_info[node_name]['is_single_replica'] = True
            
            logger.info(f"SSH Retrieved Node Pod Info: {json.dumps(node_info)}")
            return node_info
            
        except Exception as e:
            logger.error(f"Error getting node pod info via SSH: {e}")
            return {}
    def _get_node_name_from_instance(self, instance_id: str) -> str:
        """Get Kubernetes node name from EC2 instance ID"""
        try:
            response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
            if response['Reservations']:
                instance = response['Reservations'][0]['Instances'][0]
                private_dns = instance.get('PrivateDnsName', '')
                # K3s typically uses hostname as node name
                return private_dns.split('.')[0] if private_dns else instance_id
        except Exception as e:
            logger.warning(f"Failed to get node name for instance {instance_id}: {e}")
            return None
    
    def _wait_for_nodes_ready(self, instance_ids: List[str], timeout: int = 300) -> List[str]:
        """
        Wait for new nodes to join cluster and become Ready
        
        Args:
            instance_ids: List of EC2 instance IDs
            timeout: Maximum time to wait in seconds (default: 5 minutes)
        
        Returns:
            List of ready node names
        """
        logger.info(f"Waiting for {len(instance_ids)} nodes to join and become Ready (timeout: {timeout}s)")
        
        try:
            # Load Kubernetes config
            if not self._load_kube_config():
                logger.warning("_wait_for_nodes_ready: Failed to load kube config")
                return []

            v1 = client.CoreV1Api()
            
            ready_nodes = []
            start_time = time.time()
            
            # Get node names from instance IDs
            expected_nodes = {}
            for instance_id in instance_ids:
                node_name = self._get_node_name_from_instance(instance_id)
                if node_name:
                    expected_nodes[node_name] = False  # Not ready yet
            
            logger.info(f"Expecting nodes: {list(expected_nodes.keys())}")
            
            # Poll for node readiness
            while (time.time() - start_time) < timeout:
                try:
                    nodes = v1.list_node()
                    
                    for node in nodes.items:
                        node_name = node.metadata.name
                        
                        # Check if this is one of our expected nodes
                        if node_name in expected_nodes and not expected_nodes[node_name]:
                            # Check Ready condition
                            for condition in node.status.conditions:
                                if condition.type == "Ready" and condition.status == "True":
                                    logger.info(f"Node {node_name} is Ready")
                                    expected_nodes[node_name] = True
                                    ready_nodes.append(node_name)
                                    break
                    
                    # Check if all nodes are ready
                    if all(expected_nodes.values()):
                        logger.info(f"All {len(ready_nodes)} nodes are Ready")
                        return ready_nodes
                    
                    # Wait before next poll
                    time.sleep(5)
                    
                except ApiException as e:
                    logger.warning(f"Kubernetes API error while checking node status: {e}")
                    time.sleep(5)
            
            # Timeout reached
            not_ready = [name for name, ready in expected_nodes.items() if not ready]
            if not_ready:
                logger.warning(f"Timeout waiting for nodes. Not ready: {not_ready}, Ready: {ready_nodes}")
            
            return ready_nodes
            
        except Exception as e:
            logger.error(f"Error waiting for nodes to become ready: {e}")
            # Return empty list on error - don't fail the scaling operation
            return []
    
    def _get_cluster_id(self) -> str:
        """Get cluster ID from environment or tags"""
        return os.environ.get('CLUSTER_ID', 'node-fleet-cluster')
    
    def _get_ssh_key(self) -> str:
        """Retrieve SSH key from Secrets Manager"""
        try:
            logger.info("Retrieving SSH key from Secrets Manager...")
            sm = boto3.client('secretsmanager')
            response = sm.get_secret_value(SecretId='node-fleet/ssh-key')
            return response['SecretString']
        except Exception as e:
            logger.error(f"Failed to get SSH key: {e}")
            return None

    def _get_master_ip(self) -> str:
        """Dynamically resolve master node private IP via EC2 tags"""
        try:
            response = self.ec2_client.describe_instances(
                Filters=[
                    {'Name': 'tag:Role', 'Values': ['k3s-master']},
                    {'Name': 'instance-state-name', 'Values': ['running']}
                ]
            )
            reservations = response.get('Reservations', [])
            if not reservations or not reservations[0].get('Instances'):
                raise ValueError("No running k3s-master instance found")
            ip = reservations[0]['Instances'][0]['PrivateIpAddress']
            logger.info(f"Resolved master IP: {ip}")
            return ip
        except Exception as e:
            logger.error(f"Failed to resolve master IP dynamically: {e}")
            raise

    def _execute_master_command(self, command: str) -> bool:
        """Execute command on master node via SSH"""
        import paramiko
        import io
        
        try:
            # Get SSH key
            private_key_str = self._get_ssh_key()
            if not private_key_str:
                return False
                
            private_key = paramiko.RSAKey.from_private_key(io.StringIO(private_key_str))
            
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Connect to Master Private IP (resolved dynamically via EC2 tags)
            master_ip = self._get_master_ip()
            logger.info(f"SSH Connecting to master {master_ip}...")
            client.connect(hostname=master_ip, username="ubuntu", pkey=private_key, timeout=10)
            
            logger.info(f"Executing: {command}")
            stdin, stdout, stderr = client.exec_command(command)
            exit_status = stdout.channel.recv_exit_status()
            
            out_str = stdout.read().decode().strip()
            err_str = stderr.read().decode().strip()
            
            if exit_status == 0:
                logger.info(f"Command success: {out_str}")
                client.close()
                return True
            else:
                logger.error(f"Command failed (exit {exit_status}): {err_str}")
                client.close()
                return False
                
        except Exception as e:
            logger.error(f"SSH Execution Error: {e}")
            return False

    def _drain_node(self, node_name: str, instance_id: str, timeout: int = 300) -> str:
        """
        Drain node asynchronously via SSM Run Command on master.
        Returns SSM command ID (non-blocking — Lambda does NOT wait for drain to finish).
        The next Lambda invocation calls complete_pending_drains() to check status and terminate.
        """
        logger.info(f"Initiating async drain for {node_name} (instance {instance_id}) via SSM...")

        master_instance_id = self._get_master_instance_id()
        if not master_instance_id:
            logger.error("Cannot find master instance ID for SSM drain")
            return None

        cmd = (
            f"sudo k3s kubectl cordon {node_name} && "
            f"sudo k3s kubectl drain {node_name} "
            f"--ignore-daemonsets --delete-emptydir-data --force --timeout={timeout}s && "
            f"echo 'DRAIN_COMPLETE:{node_name}'"
        )

        try:
            ssm_client = boto3.client('ssm')
            response = ssm_client.send_command(
                InstanceIds=[master_instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [cmd]},
                Comment=f"K3s drain {node_name} before termination",
                TimeoutSeconds=timeout + 60,  # SSM timeout > drain timeout
            )
            command_id = response['Command']['CommandId']
            logger.info(f"SSM drain command sent: {command_id} for node {node_name}")
            return command_id
        except Exception as e:
            logger.error(f"Failed to send SSM drain command for {node_name}: {e}")
            return None

    def _check_drain_status(self, command_id: str, instance_id: str) -> str:
        """
        Check SSM command status for a pending drain.
        Returns: 'Success', 'InProgress', 'Pending', or 'Failed'
        """
        try:
            ssm_client = boto3.client('ssm')
            response = ssm_client.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )
            status = response.get('Status', 'Unknown')
            logger.info(f"SSM drain command {command_id} status: {status}")
            return status
        except Exception as e:
            logger.error(f"Failed to check SSM command {command_id}: {e}")
            return 'Failed'

    def _get_master_instance_id(self) -> str:
        """Get master node EC2 instance ID by tag"""
        try:
            response = self.ec2_client.describe_instances(
                Filters=[
                    {'Name': 'tag:Role', 'Values': ['k3s-master']},
                    {'Name': 'instance-state-name', 'Values': ['running']}
                ]
            )
            reservations = response.get('Reservations', [])
            if not reservations or not reservations[0].get('Instances'):
                raise ValueError("No running k3s-master instance found")
            instance_id = reservations[0]['Instances'][0]['InstanceId']
            logger.info(f"Resolved master instance ID: {instance_id}")
            return instance_id
        except Exception as e:
            logger.error(f"Failed to resolve master instance ID: {e}")
            return None

    def complete_pending_drains(self, state_manager) -> List[str]:
        """
        Check all pending drain operations in DynamoDB. If drain is complete,
        terminate the instance and remove it from the pending list.
        Called at start of each Lambda invocation BEFORE acquiring lock.
        Returns list of newly terminated instance IDs.
        """
        terminated = []
        pending = state_manager.get_pending_drains()
        if not pending:
            return terminated

        logger.info(f"Checking {len(pending)} pending drain(s): {[p['instance_id'] for p in pending]}")

        for drain_info in pending:
            instance_id = drain_info['instance_id']
            node_name = drain_info['node_name']
            command_id = drain_info['command_id']
            master_instance_id = drain_info.get('master_instance_id')

            if not master_instance_id:
                master_instance_id = self._get_master_instance_id()

            if not master_instance_id:
                logger.warning(f"Cannot check drain for {node_name}: no master instance ID")
                continue

            status = self._check_drain_status(command_id, master_instance_id)

            if status == 'Success':
                logger.info(f"Drain complete for {node_name} — terminating {instance_id}")
                try:
                    self.ec2_client.terminate_instances(InstanceIds=[instance_id])
                    self._delete_node(node_name)
                    state_manager.clear_drain_instance(instance_id)
                    terminated.append(instance_id)
                    logger.info(f"Terminated {instance_id} after successful drain")
                except Exception as e:
                    logger.error(f"Failed to terminate {instance_id} after drain: {e}")
            elif status in ('InProgress', 'Pending', 'Delayed'):
                elapsed = int(time.time()) - drain_info.get('start_time', int(time.time()))
                logger.info(f"Drain still in progress for {node_name} (elapsed: {elapsed}s)")
            else:
                # Failed, Cancelled, TimedOut
                logger.warning(
                    f"Drain {status} for {node_name} ({command_id}). "
                    f"Removing from pending list without terminating."
                )
                state_manager.clear_drain_instance(instance_id)

        return terminated

    def _load_kube_config(self) -> bool:
        """
        Load Kubernetes configuration from available sources:
        1. Environment variable KUBECONFIG
        2. In-cluster config
        3. Secrets Manager (node-fleet/kubeconfig)
        """
        # 1. Check if already loaded/available via env var
        kubeconfig_path = os.environ.get('KUBECONFIG')
        if kubeconfig_path and os.path.exists(kubeconfig_path):
            try:
                config.load_kube_config(config_file=kubeconfig_path)
                # Force disable SSL verification
                c = client.Configuration.get_default_copy()
                c.verify_ssl = False
                client.Configuration.set_default(c)
                return True
            except Exception as e:
                logger.warning(f"Failed to load from KUBECONFIG={kubeconfig_path}: {e}")

        # 2. Try in-cluster config
        try:
            config.load_incluster_config()
            return True
        except Exception:
            pass
            
        # 3. Try Secrets Manager
        try:
            logger.info("Attempting to load kubeconfig from Secrets Manager (node-fleet/kubeconfig)...")
            sm_client = boto3.client('secretsmanager')
            secret_response = sm_client.get_secret_value(SecretId='node-fleet/kubeconfig')
            kubeconfig_content = secret_response['SecretString']
            
            # Write to temporary file
            # We use a fixed path /tmp/kubeconfig to reuse it across warm starts
            tmp_path = '/tmp/kubeconfig'
            with open(tmp_path, 'w') as f:
                f.write(kubeconfig_content)
            
            os.environ['KUBECONFIG'] = tmp_path
            
            config.load_kube_config(config_file=tmp_path)
            
            # Force disable SSL verification
            c = client.Configuration.get_default_copy()
            c.verify_ssl = False
            client.Configuration.set_default(c)
            
            logger.info(f"Successfully loaded kubeconfig from Secrets Manager to {tmp_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load kubeconfig from all sources: {e}")
            return False

    def _delete_node(self, node_name: str) -> bool:
        """Delete node using SSH to master"""
        logger.info(f"Deleting node {node_name} object via SSH...")
        cmd = f"sudo k3s kubectl delete node {node_name} --ignore-not-found"
        return self._execute_master_command(cmd)
