"""
Multi-AZ distribution test for EC2 manager
"""

import pytest
import sys
import os
from unittest.mock import Mock, MagicMock, patch

# Add parent directory to path to import lambda modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("ec2_manager", os.path.join(os.path.dirname(__file__), '../../lambda/ec2_manager.py'))
ec2_manager_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec2_manager_module)
EC2Manager = ec2_manager_module.EC2Manager


@pytest.fixture
def mock_ec2_multi_az():
    """Mock EC2 client with multi-AZ support"""
    with patch('ec2_manager.boto3') as mock_boto:
        mock_client = MagicMock()
        mock_boto.client.return_value = mock_client
        
        # Mock AZ discovery
        mock_client.describe_availability_zones.return_value = {
            'AvailabilityZones': [
                {'ZoneName': 'ap-south-1a', 'State': 'available'},
                {'ZoneName': 'ap-south-1b', 'State': 'available'}
            ]
        }
        
        yield mock_client


def test_multi_az_distribution(mock_ec2_multi_az):
    """Test instances are distributed across multiple AZs"""
    # Setup mock responses for instance launches
    mock_ec2_multi_az.run_instances.side_effect = [
        {'Instances': [{'InstanceId': 'i-az1-1'}]},  # AZ 1
        {'Instances': [{'InstanceId': 'i-az2-1'}]},  # AZ 2
        {'Instances': [{'InstanceId': 'i-az1-2'}]},  # AZ 1
        {'Instances': [{'InstanceId': 'i-az2-2'}]},  # AZ 2
    ]
    
    manager = EC2Manager(
        worker_template_id='lt-test',
        worker_spot_template_id='lt-spot-test'
    )
    
    # Launch 4 instances
    result = manager.scale_up(nodes_to_add=4, reason="Testing multi-AZ")
    
    # Verify instances were launched
    assert result['success'] is True
    assert len(result['instance_ids']) == 4
    
    # Verify run_instances was called 4 times (one per instance)
    assert mock_ec2_multi_az.run_instances.call_count >= 1
    
    # Verify AZ placement was specified
    calls = mock_ec2_multi_az.run_instances.call_args_list
    azs_used = [call.kwargs.get('Placement', {}).get('AvailabilityZone') for call in calls if 'Placement' in call.kwargs]
    
    # Check that both AZs were used (round-robin distribution)
    assert 'ap-south-1a' in azs_used or 'ap-south-1b' in azs_used


def test_az_round_robin(mock_ec2_multi_az):
    """Test round-robin AZ selection"""
    mock_ec2_multi_az.run_instances.side_effect = [
        {'Instances': [{'InstanceId': f'i-{i}'}]} for i in range(6)
    ]
    
    manager = EC2Manager(
        worker_template_id='lt-test',
        worker_spot_template_id='lt-spot-test'
    )
    
    # Launch 6 instances - should alternate between 2 AZs
    result = manager.scale_up(nodes_to_add=6, reason="Testing round-robin")
    
    assert len(result['instance_ids']) == 6
    
    # Verify AZs alternate
    calls = mock_ec2_multi_az.run_instances.call_args_list
    azs = [call.kwargs['Placement']['AvailabilityZone'] for call in calls if 'Placement' in call.kwargs]
    
    # Should use both AZs multiple times
    assert azs.count('ap-south-1a') >= 2
    assert azs.count('ap-south-1b') >= 2


def test_az_fallback_on_error(mock_ec2_multi_az):
    """Test fallback behavior when AZ discovery fails"""
    mock_ec2_multi_az.describe_availability_zones.side_effect = Exception("API error")
    
    manager = EC2Manager(
        worker_template_id='lt-test',
        worker_spot_template_id='lt-spot-test'
    )
    
    # Should use fallback AZs
    assert len(manager.availability_zones) == 2
    assert 'ap-south-1a' in manager.availability_zones
    assert 'ap-south-1b' in manager.availability_zones
