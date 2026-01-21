"""
Spot instance management helper for cost-optimized worker scaling.
Implements 70% spot / 30% on-demand mix with interruption handling.
"""

import boto3
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def calculate_spot_ondemand_mix(current_nodes: int, desired_nodes: int, existing_spot_count: int, existing_ondemand_count: int) -> Dict[str, int]:
    """
    Calculate how many spot vs on-demand instances to launch to maintain 70/30 ratio.
    
    Args:
        current_nodes: Current total node count
        desired_nodes: Target node count after scaling
        existing_spot_count: Current number of spot instances
        existing_ondemand_count: Current number of on-demand instances
    
    Returns:
        Dict with 'spot' and 'ondemand' keys indicating how many of each to launch
    """
    nodes_to_add = desired_nodes - current_nodes
    if nodes_to_add <= 0:
        return {'spot': 0, 'ondemand': 0}
    
    # Target: 70% spot, 30% on-demand
    target_spot_ratio = 0.70
    
    # Calculate ideal total counts
    # Ensure desired_nodes is int (boto3 DynamoDB returns Decimal)
    desired_nodes_int = int(desired_nodes)
    ideal_total_spot = int(desired_nodes_int * target_spot_ratio)
    ideal_total_ondemand = desired_nodes_int - ideal_total_spot
    
    # Calculate how many more of each type we need
    spot_to_add = max(0, ideal_total_spot - existing_spot_count)
    ondemand_to_add = max(0, ideal_total_ondemand - existing_ondemand_count)
    
    # Ensure we don't exceed the desired total
    total_to_add = spot_to_add + ondemand_to_add
    if total_to_add > nodes_to_add:
        # Prioritize spot instances
        if spot_to_add > nodes_to_add:
            spot_to_add = nodes_to_add
            ondemand_to_add = 0
        else:
            ondemand_to_add = nodes_to_add - spot_to_add
    elif total_to_add < nodes_to_add:
        # Add remaining as spot (cheaper)
        spot_to_add += (nodes_to_add - total_to_add)
    
    logger.info(f"Scaling mix: adding {spot_to_add} spot + {ondemand_to_add} on-demand instances")
    logger.info(f"New totals will be: {existing_spot_count + spot_to_add} spot + {existing_ondemand_count + ondemand_to_add} on-demand")
    
    return {
        'spot': spot_to_add,
        'ondemand': ondemand_to_add
    }


def get_spot_interruption_notices(cluster_id: str) -> List[str]:
    """
    Check for spot instance interruption notices via EC2 metadata service.
    In production, this would query instance metadata for interruption warnings.
    
    Args:
        cluster_id: K3s cluster identifier
    
    Returns:
        List of instance IDs scheduled for interruption
    """
    ec2_client = boto3.client('ec2')
    
    try:
        # Find instances with spot interruption tags or scheduled events
        response = ec2_client.describe_instances(
            Filters=[
                {'Name': 'tag:Cluster', 'Values': [cluster_id]},
                {'Name': 'tag:Role', 'Values': ['k3s-worker']},
                {'Name': 'instance-lifecycle', 'Values': ['spot']},
                {'Name': 'instance-state-name', 'Values': ['running']}
            ]
        )
        
        interrupted_instances = []
        
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instance_id = instance['InstanceId']
                
                # Check for interruption tags (set by EventBridge rule)
                tags = {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])}
                if tags.get('SpotInterruption') == 'true':
                    interrupted_instances.append(instance_id)
                    logger.warning(f"Spot instance {instance_id} has interruption notice")
        
        return interrupted_instances
        
    except Exception as e:
        logger.error(f"Error checking spot interruptions: {e}")
        return []


def handle_spot_interruption(instance_id: str, cluster_id: str) -> bool:
    """
    Handle spot instance interruption by draining pods and launching replacement.
    
    Args:
        instance_id: Instance being interrupted
        cluster_id: K3s cluster identifier
    
    Returns:
        True if handled successfully
    """
    try:
        logger.info(f"Handling spot interruption for {instance_id}")
        
        # Tag instance for tracking
        ec2_client = boto3.client('ec2')
        ec2_client.create_tags(
            Resources=[instance_id],
            Tags=[
                {'Key': 'SpotInterruptionHandled', 'Value': 'true'},
                {'Key': 'InterruptionTime', 'Value': datetime.utcnow().isoformat()}
            ]
        )
        
        # Note: Actual pod draining happens in k3s_helper.py drain_node()
        # This function just tags and triggers replacement
        
        logger.info(f"Spot instance {instance_id} marked for draining and replacement")
        return True
        
    except Exception as e:
        logger.error(f"Error handling spot interruption for {instance_id}: {e}")
        return False


def get_spot_price_recommendations(instance_type: str, availability_zones: List[str]) -> Dict[str, float]:
    """
    Get current spot price recommendations for instance type across AZs.
    
    Args:
        instance_type: EC2 instance type (e.g., 't3.medium')
        availability_zones: List of AZs to check
    
    Returns:
        Dict mapping AZ to current spot price
    """
    ec2_client = boto3.client('ec2')
    
    try:
        response = ec2_client.describe_spot_price_history(
            InstanceTypes=[instance_type],
            ProductDescriptions=['Linux/UNIX'],
            MaxResults=len(availability_zones) * 2,
            AvailabilityZones=availability_zones
        )
        
        # Get most recent price for each AZ
        az_prices = {}
        for price_info in response['SpotPriceHistory']:
            az = price_info['AvailabilityZone']
            price = float(price_info['SpotPrice'])
            
            if az not in az_prices or price < az_prices[az]:
                az_prices[az] = price
        
        logger.info(f"Spot prices for {instance_type}: {az_prices}")
        return az_prices
        
    except Exception as e:
        logger.error(f"Error fetching spot prices: {e}")
        return {}


def should_use_spot_instance(current_spot_count: int, total_nodes: int, target_ratio: float = 0.70) -> bool:
    """
    Determine if next instance should be spot or on-demand based on current ratio.
    
    Args:
        current_spot_count: Current number of spot instances
        total_nodes: Total number of nodes in cluster
        target_ratio: Target spot instance ratio (default 70%)
    
    Returns:
        True if next instance should be spot
    """
    if total_nodes == 0:
        return True  # Start with spot
    
    current_ratio = current_spot_count / total_nodes
    
    # Use spot if below target ratio
    return current_ratio < target_ratio
