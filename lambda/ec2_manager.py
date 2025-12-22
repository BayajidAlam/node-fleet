"""
EC2 manager for scaling worker nodes
Handles instance launch and termination with Multi-AZ support and spot instances
"""

import logging
import boto3
import subprocess
from typing import Dict, List
from botocore.exceptions import ClientError
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
            
            return {
                "success": True,
                "instance_ids": instance_ids,
                "spot_count": spot_count,
                "ondemand_count": ondemand_count
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
        """Select instances for termination (prefer spot instances)"""
        # Separate spot and on-demand
        spot_instances = [i for i in instances if i.get('InstanceLifecycle') == 'spot']
        ondemand_instances = [i for i in instances if i.get('InstanceLifecycle') != 'spot']
        
        selected = []
        
        # Prefer removing spot instances first
        selected.extend(spot_instances[:count])
        remaining = count - len(selected)
        
        if remaining > 0:
            selected.extend(ondemand_instances[:remaining])
        
        return selected[:count]
    
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
            # Note: In production, this would use kubectl via the K8s API
            # For now, we log the action
            logger.info(f"Would drain node {node_name} with timeout {timeout}s")
            
            # In real implementation:
            # kubectl drain {node_name} --ignore-daemonsets --delete-emptydir-data --timeout={timeout}s
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to drain node {node_name}: {str(e)}")
            return False
