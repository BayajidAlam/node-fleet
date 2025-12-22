"""
EC2 manager for scaling worker nodes
Handles instance launch and termination with Multi-AZ support and spot instances
"""

import logging
import boto3
import subprocess
import os
import time
from typing import Dict, List
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
        # Multi-AZ: Subnet IDs for ap-south-1a and ap-south-1b
        self.available_subnets = self._get_cluster_subnets()
    
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
        
        # Calculate smart spot/on-demand mix to maintain 70/30 ratio
        mix = calculate_spot_ondemand_mix(
            current_nodes=current_total,
            desired_nodes=current_total + nodes_to_add,
            existing_spot_count=current_spot_count,
            existing_ondemand_count=current_ondemand_count
        )
        
        spot_count = mix['spot']
        ondemand_count = mix['ondemand']
        
        instance_ids = []
        
        try:
            # Launch spot instances
            if spot_count > 0:
                logger.info(f"Launching {spot_count} spot instances")
                spot_instances = self._launch_instances(
                    template_id=self.worker_spot_template_id,
                    count=spot_count,
                    instance_type="spot"
                )
                instance_ids.extend(spot_instances)
            
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
    
    def scale_down(self, nodes_to_remove: int, reason: str) -> Dict:
        """
        Safely remove worker nodes
        
        Args:
            nodes_to_remove: Number of nodes to remove
            reason: Reason for scaling down
        
        Returns:
            Dictionary with terminated instance details
        """
        logger.info(f"Scaling down: Removing {nodes_to_remove} nodes. Reason: {reason}")
        
        try:
            # Get all worker nodes
            worker_instances = self._get_worker_instances()
            
            if len(worker_instances) <= nodes_to_remove:
                logger.warning(f"Not enough workers to remove. Current: {len(worker_instances)}, Requested: {nodes_to_remove}")
                nodes_to_remove = max(0, len(worker_instances) - 1)  # Keep at least 1 worker
            
            if nodes_to_remove == 0:
                return {"success": True, "instance_ids": [], "message": "No nodes removed"}
            
            # Select instances to terminate (prefer spot instances first)
            instances_to_terminate = self._select_instances_for_termination(
                worker_instances, 
                nodes_to_remove
            )
            
            terminated_ids = []
            
            for instance in instances_to_terminate:
                instance_id = instance['InstanceId']
                node_name = self._get_node_name_from_instance(instance_id)
                
                if node_name:
                    # Drain node before termination
                    logger.info(f"Draining node {node_name}")
                    if not self._drain_node(node_name):
                        logger.warning(f"Failed to drain {node_name}, skipping termination")
                        continue
                
                # Terminate instance
                logger.info(f"Terminating instance {instance_id}")
                self.ec2_client.terminate_instances(InstanceIds=[instance_id])
                terminated_ids.append(instance_id)
            
            logger.info(f"Successfully terminated {len(terminated_ids)} instances: {terminated_ids}")
            
            return {
                "success": True,
                "instance_ids": terminated_ids
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
        Priority: spot instances > nodes with fewer pods
        """
        logger.info("Selecting instances for termination using pod-aware strategy")
        
        # Separate spot and on-demand
        spot_instances = [i for i in instances if i.get('InstanceLifecycle') == 'spot']
        ondemand_instances = [i for i in instances if i.get('InstanceLifecycle') != 'spot']
        
        # Get pod counts for all nodes
        instance_pod_counts = self._get_pod_counts_per_node()
        logger.info(f"Pod distribution across nodes: {instance_pod_counts}")
        
        # Sort spot instances by pod count (ascending - prefer nodes with fewer pods)
        spot_instances_sorted = sorted(
            spot_instances,
            key=lambda i: instance_pod_counts.get(self._get_node_name_from_instance(i['InstanceId']), 0)
        )
        
        # Sort on-demand instances by pod count (ascending)
        ondemand_instances_sorted = sorted(
            ondemand_instances,
            key=lambda i: instance_pod_counts.get(self._get_node_name_from_instance(i['InstanceId']), 0)
        )
        
        selected = []
        
        # Prefer removing spot instances first (with fewest pods)
        selected.extend(spot_instances_sorted[:count])
        remaining = count - len(selected)
        
        # Fill remaining from on-demand (with fewest pods)
        if remaining > 0:
            selected.extend(ondemand_instances_sorted[:remaining])
        
        # Log selection rationale
        for instance in selected[:count]:
            node_name = self._get_node_name_from_instance(instance['InstanceId'])
            pod_count = instance_pod_counts.get(node_name, 0)
            instance_type = "spot" if instance.get('InstanceLifecycle') == 'spot' else "on-demand"
            logger.info(f"Selected {instance['InstanceId']} ({instance_type}, {pod_count} pods) for termination")
        
        return selected[:count]
    
    def _get_pod_counts_per_node(self) -> Dict[str, int]:
        """
        Get pod count for each node in the cluster
        
        Returns:
            Dictionary mapping node name to pod count (excluding system pods)
        """
        try:
            config.load_incluster_config()
            v1 = client.CoreV1Api()
            
            pod_counts = {}
            
            # Get all pods
            pods = v1.list_pod_for_all_namespaces()
            
            for pod in pods.items:
                # Skip pods in kube-system namespace (they're infrastructure)
                if pod.metadata.namespace == 'kube-system':
                    continue
                
                # Skip completed/failed pods
                if pod.status.phase in ['Succeeded', 'Failed']:
                    continue
                
                node_name = pod.spec.node_name
                if node_name:
                    pod_counts[node_name] = pod_counts.get(node_name, 0) + 1
            
            return pod_counts
            
        except Exception as e:
            logger.error(f"Error getting pod counts per node: {e}")
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
        except:
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
            config.load_incluster_config()
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
        import os
        return os.environ.get('CLUSTER_ID', 'node-fleet-cluster')
    
    def _drain_node(self, node_name: str, timeout: int = 300) -> bool:
        """
        Drain Kubernetes node before termination
        
        Args:
            node_name: K8s node name
            timeout: Drain timeout in seconds
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Load kubeconfig from environment or default location
            kubeconfig_path = os.environ.get('KUBECONFIG', '/tmp/kubeconfig')
            if os.path.exists(kubeconfig_path):
                config.load_kube_config(config_file=kubeconfig_path)
            else:
                # Try in-cluster config (if Lambda runs with service account)
                try:
                    config.load_incluster_config()
                except:
                    logger.warning(f"No kubeconfig found, skipping drain for {node_name}")
                    return True
            
            v1 = client.CoreV1Api()
            
            # Step 1: Cordon the node (mark as unschedulable)
            logger.info(f"Cordoning node {node_name}")
            body = {
                "spec": {
                    "unschedulable": True
                }
            }
            v1.patch_node(node_name, body)
            
            # Step 2: Get all pods on the node
            logger.info(f"Getting pods on node {node_name}")
            field_selector = f"spec.nodeName={node_name}"
            pods = v1.list_pod_for_all_namespaces(field_selector=field_selector)
            
            # Step 3: Delete pods (ignore daemonsets, static pods, check StatefulSets)
            pods_to_delete = []
            statefulset_pods = []
            
            for pod in pods.items:
                skip_pod = False
                
                # Check owner references
                if pod.metadata.owner_references:
                    for owner in pod.metadata.owner_references:
                        # Skip DaemonSet pods
                        if owner.kind == "DaemonSet":
                            logger.info(f"Skipping DaemonSet pod {pod.metadata.name}")
                            skip_pod = True
                            break
                        
                        # Identify StatefulSet pods for special handling
                        if owner.kind == "StatefulSet":
                            statefulset_pods.append(pod)
                            logger.warning(f"StatefulSet pod detected: {pod.metadata.namespace}/{pod.metadata.name}")
                            # Don't skip yet, will handle carefully
                
                if skip_pod:
                    continue
                
                # Check for static pods (mirror pods)
                if pod.metadata.annotations and 'kubernetes.io/config.mirror' in pod.metadata.annotations:
                    logger.info(f"Skipping static pod {pod.metadata.name}")
                    continue
                
                pods_to_delete.append(pod)
            
            # Check if draining StatefulSet pods is safe
            if statefulset_pods:
                logger.warning(f"Found {len(statefulset_pods)} StatefulSet pods - verifying replicas on other nodes")
                
                # Verify each StatefulSet has replicas on other nodes
                for sts_pod in statefulset_pods:
                    is_safe = self._verify_statefulset_replicas(sts_pod, node_name)
                    
                    if not is_safe:
                        logger.error(
                            f"UNSAFE: StatefulSet {sts_pod.metadata.namespace}/{sts_pod.metadata.name} "
                            f"has no other replicas running. Aborting drain to prevent data loss."
                        )
                        # Uncordon the node since we're not proceeding
                        self._uncordon_node(node_name)
                        return False
                    
                    logger.info(
                        f"✓ StatefulSet {sts_pod.metadata.namespace}/{sts_pod.metadata.name} "
                        f"has replicas on other nodes - safe to drain"
                    )
            
            logger.info(f"Draining {len(pods_to_delete)} pods from node {node_name}")
            
            # Step 4: Evict pods with grace period
            for pod in pods_to_delete:
                try:
                    # Use eviction API for graceful pod termination
                    eviction = client.V1Eviction(
                        metadata=client.V1ObjectMeta(
                            name=pod.metadata.name,
                            namespace=pod.metadata.namespace
                        ),
                        delete_options=client.V1DeleteOptions(
                            grace_period_seconds=30
                        )
                    )
                    v1.create_namespaced_pod_eviction(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                        body=eviction
                    )
                    logger.info(f"Evicted pod {pod.metadata.namespace}/{pod.metadata.name}")
                except ApiException as e:
                    if e.status == 404:
                        logger.info(f"Pod {pod.metadata.name} already deleted")
                    elif e.status == 429:
                        logger.warning(f"PodDisruptionBudget prevents eviction of {pod.metadata.name}")
                    else:
                        logger.error(f"Failed to evict pod {pod.metadata.name}: {e}")
            
            logger.info(f"Successfully drained node {node_name}")
            return True
            
        except ApiException as e:
            logger.error(f"Kubernetes API error while draining node {node_name}: {e}")
            # Uncordon node on failure to restore scheduling
            self._uncordon_node(node_name)
            return False
        except Exception as e:
            logger.error(f"Failed to drain node {node_name}: {str(e)}")
            # Uncordon node on failure to restore scheduling
            self._uncordon_node(node_name)
            return False
    
    def _verify_statefulset_replicas(self, pod, current_node: str) -> bool:
        """
        Verify that a StatefulSet has other running replicas on different nodes
        
        Args:
            pod: StatefulSet pod object
            current_node: Node being drained
        
        Returns:
            True if other replicas exist, False otherwise
        """
        try:
            config.load_incluster_config()
            v1 = client.CoreV1Api()
            apps_v1 = client.AppsV1Api()
            
            # Get StatefulSet name from pod
            statefulset_name = None
            for owner in pod.metadata.owner_references:
                if owner.kind == "StatefulSet":
                    statefulset_name = owner.name
                    break
            
            if not statefulset_name:
                logger.warning(f"Could not determine StatefulSet name for pod {pod.metadata.name}")
                return True  # Default to safe
            
            # Get StatefulSet
            sts = apps_v1.read_namespaced_stateful_set(
                name=statefulset_name,
                namespace=pod.metadata.namespace
            )
            
            desired_replicas = sts.spec.replicas
            
            # Get all pods for this StatefulSet
            label_selector = ",".join([f"{k}={v}" for k, v in sts.spec.selector.match_labels.items()])
            sts_pods = v1.list_namespaced_pod(
                namespace=pod.metadata.namespace,
                label_selector=label_selector
            )
            
            # Count running replicas on other nodes
            other_node_replicas = 0
            for sts_pod in sts_pods.items:
                # Skip the pod being drained
                if sts_pod.metadata.name == pod.metadata.name:
                    continue
                
                # Check if running on a different node and is Ready
                if (sts_pod.spec.node_name != current_node and 
                    sts_pod.status.phase == "Running"):
                    
                    # Check pod readiness
                    is_ready = False
                    if sts_pod.status.conditions:
                        for condition in sts_pod.status.conditions:
                            if condition.type == "Ready" and condition.status == "True":
                                is_ready = True
                                break
                    
                    if is_ready:
                        other_node_replicas += 1
            
            logger.info(
                f"StatefulSet {statefulset_name}: "
                f"Desired={desired_replicas}, "
                f"Running on other nodes={other_node_replicas}"
            )
            
            # Safe to drain if there's at least 1 replica on another node
            return other_node_replicas > 0
            
        except Exception as e:
            logger.error(f"Error verifying StatefulSet replicas: {e}")
            # On error, default to safe (don't block drain for transient errors)
            return True
    
    def _uncordon_node(self, node_name: str) -> bool:
        """
        Uncordon a node (mark as schedulable)
        
        Args:
            node_name: K8s node name
        
        Returns:
            True if successful, False otherwise
        """
        try:
            config.load_incluster_config()
            v1 = client.CoreV1Api()
            
            logger.info(f"Uncordoning node {node_name}")
            body = {
                "spec": {
                    "unschedulable": False
                }
            }
            v1.patch_node(node_name, body)
            logger.info(f"Successfully uncordoned node {node_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to uncordon node {node_name}: {e}")
            return False
