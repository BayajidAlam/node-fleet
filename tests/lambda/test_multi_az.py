"""
Multi-AZ distribution test for EC2 manager
"""

import pytest
import sys
import os
from unittest.mock import Mock, MagicMock, patch

# Add parent directory and lambda directory to path to import lambda modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../lambda')))

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("ec2_manager", os.path.join(os.path.dirname(__file__), '../../lambda/ec2_manager.py'))
ec2_manager_module = importlib.util.module_from_spec(spec)

# Register in sys.modules so patch('ec2_manager.boto3') works
sys.modules['ec2_manager'] = ec2_manager_module
spec.loader.exec_module(ec2_manager_module)
EC2Manager = ec2_manager_module.EC2Manager


@pytest.fixture
def mock_ec2_multi_az():
    """Mock EC2 client with multi-AZ support"""
    with patch('ec2_manager.boto3') as mock_boto:
        mock_client = MagicMock()
        mock_boto.client.return_value = mock_client
        
        # Mock subnet discovery (Multi-AZ)
        mock_client.describe_subnets.return_value = {
            'Subnets': [
                {'SubnetId': 'subnet-az1', 'AvailabilityZone': 'ap-south-1a'},
                {'SubnetId': 'subnet-az2', 'AvailabilityZone': 'ap-south-1b'}
            ]
        }
        
        yield mock_client


def test_multi_az_distribution(mock_ec2_multi_az):
    """Test instances are distributed across multiple AZs"""
    # Setup mock responses for instance launches
    mock_ec2_multi_az.run_instances.side_effect = [
        {'Instances': [{'InstanceId': 'i-az1-1', 'SubnetId': 'subnet-az1'}]},  # AZ 1
        {'Instances': [{'InstanceId': 'i-az2-1', 'SubnetId': 'subnet-az2'}]},  # AZ 2
        {'Instances': [{'InstanceId': 'i-az1-2', 'SubnetId': 'subnet-az1'}]},  # AZ 1
        {'Instances': [{'InstanceId': 'i-az2-2', 'SubnetId': 'subnet-az2'}]},  # AZ 2
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
    
    # Verify SubnetId placement was specified (which implies AZ)
    calls = mock_ec2_multi_az.run_instances.call_args_list
    subnets_used = [call.kwargs.get('SubnetId') for call in calls]
    
    # Check that both subnets were used
    assert 'subnet-az1' in subnets_used
    assert 'subnet-az2' in subnets_used


def test_az_round_robin(mock_ec2_multi_az):
    """Test round-robin AZ selection"""
    mock_ec2_multi_az.run_instances.side_effect = [
        {'Instances': [{'InstanceId': f'i-{i}', 'SubnetId': 'subnet-az1' if i % 2 == 0 else 'subnet-az2'}]} 
        for i in range(6)
    ]
    
    manager = EC2Manager(
        worker_template_id='lt-test',
        worker_spot_template_id='lt-spot-test'
    )
    
    # Launch 6 instances - should alternate between 2 subnets
    result = manager.scale_up(nodes_to_add=6, reason="Testing round-robin")
    
    assert len(result['instance_ids']) == 6
    
    # Verify subnets alternate
    calls = mock_ec2_multi_az.run_instances.call_args_list
    subnets = [call.kwargs['SubnetId'] for call in calls]
    
    # Should use both subnets multiple times
    assert subnets.count('subnet-az1') >= 2
    assert subnets.count('subnet-az2') >= 2


def test_az_fallback_on_error(mock_ec2_multi_az):
    """Test fallback behavior when subnet discovery fails"""
    # Mock empty subnets
    mock_ec2_multi_az.describe_subnets.return_value = {'Subnets': []}
    
    manager = EC2Manager(
        worker_template_id='lt-test',
        worker_spot_template_id='lt-spot-test'
    )
    
    # Should have empty subnets list
    assert len(manager.available_subnets) == 0
