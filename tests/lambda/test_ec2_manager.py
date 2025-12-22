"""
Unit tests for ec2_manager module
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("ec2_manager", os.path.join(os.path.dirname(__file__), '../../lambda/ec2_manager.py'))
ec2_manager_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec2_manager_module)
EC2Manager = ec2_manager_module.EC2Manager


@pytest.fixture
def mock_ec2():
    """Mock EC2 client"""
    with patch('lambda.ec2_manager.boto3') as mock_boto:
        mock_client = MagicMock()
        mock_boto.client.return_value = mock_client
        yield mock_client


def test_scale_up_on_demand_only(mock_ec2):
    """Test scaling up with on-demand instances"""
    mock_ec2.run_instances.return_value = {
        'Instances': [
            {'InstanceId': 'i-123'},
            {'InstanceId': 'i-456'}
        ]
    }
    
    manager = EC2Manager(
        worker_template_id='lt-ondemand',
        worker_spot_template_id='lt-spot',
        spot_percentage=0  # 0% spot = all on-demand
    )
    
    result = manager.scale_up(nodes_to_add=2, reason="High CPU")
    
    assert result['success'] is True
    assert len(result['instance_ids']) == 2
    assert result['ondemand_count'] == 2
    assert result['spot_count'] == 0


def test_scale_up_mixed_instances(mock_ec2):
    """Test scaling up with mixed spot and on-demand"""
    mock_ec2.run_instances.side_effect = [
        {'Instances': [{'InstanceId': 'i-spot1'}]},  # Spot
        {'Instances': [{'InstanceId': 'i-ondemand1'}]}  # On-demand
    ]
    
    manager = EC2Manager(
        worker_template_id='lt-ondemand',
        worker_spot_template_id='lt-spot',
        spot_percentage=70
    )
    
    result = manager.scale_up(nodes_to_add=2, reason="High CPU")
    
    assert result['success'] is True
    assert result['spot_count'] == 1
    assert result['ondemand_count'] == 1


def test_scale_down_success(mock_ec2):
    """Test successful scale-down"""
    mock_ec2.describe_instances.return_value = {
        'Reservations': [{
            'Instances': [
                {
                    'InstanceId': 'i-123',
                    'PrivateDnsName': 'worker-1.internal',
                    'InstanceLifecycle': 'spot'
                },
                {
                    'InstanceId': 'i-456',
                    'PrivateDnsName': 'worker-2.internal'
                }
            ]
        }]
    }
    mock_ec2.terminate_instances.return_value = {}
    
    manager = EC2Manager(
        worker_template_id='lt-ondemand',
        worker_spot_template_id='lt-spot'
    )
    
    result = manager.scale_down(nodes_to_remove=1, reason="Low CPU")
    
    assert result['success'] is True
    assert len(result['instance_ids']) >= 1


def test_scale_down_prefers_spot(mock_ec2):
    """Test scale-down prefers removing spot instances"""
    mock_ec2.describe_instances.return_value = {
        'Reservations': [{
            'Instances': [
                {
                    'InstanceId': 'i-spot',
                    'PrivateDnsName': 'spot.internal',
                    'InstanceLifecycle': 'spot'
                },
                {
                    'InstanceId': 'i-ondemand',
                    'PrivateDnsName': 'ondemand.internal'
                }
            ]
        }]
    }
    
    manager = EC2Manager(
        worker_template_id='lt-ondemand',
        worker_spot_template_id='lt-spot'
    )
    
    instances = [
        {'InstanceId': 'i-spot', 'InstanceLifecycle': 'spot'},
        {'InstanceId': 'i-ondemand'}
    ]
    
    selected = manager._select_instances_for_termination(instances, 1)
    
    assert len(selected) == 1
    assert selected[0]['InstanceId'] == 'i-spot'


def test_get_worker_instances(mock_ec2):
    """Test getting worker instances"""
    mock_ec2.describe_instances.return_value = {
        'Reservations': [{
            'Instances': [
                {'InstanceId': 'i-1'},
                {'InstanceId': 'i-2'}
            ]
        }]
    }
    
    manager = EC2Manager(
        worker_template_id='lt-ondemand',
        worker_spot_template_id='lt-spot'
    )
    
    instances = manager._get_worker_instances()
    
    assert len(instances) == 2
    mock_ec2.describe_instances.assert_called_once()
