"""
Multi-AZ distribution logic for EC2Manager
Ensures workers are evenly distributed across availability zones
"""

from typing import List, Dict

def select_subnet_for_new_instance(existing_instances: List[Dict], available_subnets: List[str]) -> str:
    """
    Select subnet for new instance to balance across AZs
    
    Args:
        existing_instances: List of current running instances with subnet info
        available_subnets: List of subnet IDs (ap-south-1a, ap-south-1b)
    
    Returns:
        Subnet ID to use for new instance
    """
    # Count instances per subnet
    subnet_counts = {}
    for subnet_id in available_subnets:
        subnet_counts[subnet_id] = 0
    
    for instance in existing_instances:
        subnet_id = instance.get('SubnetId')
        if subnet_id in subnet_counts:
            subnet_counts[subnet_id] += 1
    
    # Return subnet with fewest instances (load balancing)
    return min(subnet_counts, key=subnet_counts.get)


def get_az_distribution(instances: List[Dict]) -> Dict[str, int]:
    """
    Get current distribution of instances across AZs
    
    Returns:
        Dictionary with AZ as key and instance count as value
    """
    az_counts = {}
    for instance in instances:
        az = instance.get('Placement', {}).get('AvailabilityZone', 'unknown')
        az_counts[az] = az_counts.get(az, 0) + 1
    
    return az_counts
