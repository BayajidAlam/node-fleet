"""
Tests for spot instance management functionality
"""

import pytest
import sys
import os
from unittest.mock import Mock, MagicMock, patch

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("spot_instance_helper", os.path.join(os.path.dirname(__file__), '../../lambda/spot_instance_helper.py'))
spot_helper_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spot_helper_module)

calculate_spot_ondemand_mix = spot_helper_module.calculate_spot_ondemand_mix
get_spot_interruption_notices = spot_helper_module.get_spot_interruption_notices
handle_spot_interruption = spot_helper_module.handle_spot_interruption
get_spot_price_recommendations = spot_helper_module.get_spot_price_recommendations
should_use_spot_instance = spot_helper_module.should_use_spot_instance


def test_calculate_spot_ondemand_mix_initial_scale():
    """Test spot/on-demand calculation when scaling from 0"""
    result = calculate_spot_ondemand_mix(
        current_nodes=0,
        desired_nodes=10,
        existing_spot_count=0,
        existing_ondemand_count=0
    )
    
    assert result['spot'] == 7  # 70% of 10
    assert result['ondemand'] == 3  # 30% of 10


def test_calculate_spot_ondemand_mix_maintain_ratio():
    """Test that scaling maintains 70/30 ratio"""
    result = calculate_spot_ondemand_mix(
        current_nodes=10,
        desired_nodes=15,
        existing_spot_count=7,
        existing_ondemand_count=3
    )
    
    # Should add to maintain 70/30: target is 10.5 spot, 4.5 on-demand
    # Actual: 10 spot (7+3), 5 on-demand (3+2) or 11 spot (7+4), 4 on-demand (3+1)
    assert result['spot'] + result['ondemand'] == 5
    assert result['spot'] >= 3  # Should add more spot


def test_calculate_spot_ondemand_mix_rebalance():
    """Test rebalancing when ratio is off"""
    result = calculate_spot_ondemand_mix(
        current_nodes=10,
        desired_nodes=20,
        existing_spot_count=3,  # Only 30% spot (too low)
        existing_ondemand_count=7  # 70% on-demand
    )
    
    # Should add mostly spot to rebalance to 70% spot
    assert result['spot'] > result['ondemand']
    assert result['spot'] + result['ondemand'] == 10


def test_calculate_spot_ondemand_mix_no_scale():
    """Test when no scaling is needed"""
    result = calculate_spot_ondemand_mix(
        current_nodes=10,
        desired_nodes=10,
        existing_spot_count=7,
        existing_ondemand_count=3
    )
    
    assert result['spot'] == 0
    assert result['ondemand'] == 0


def test_calculate_spot_ondemand_mix_scale_down():
    """Test when scaling down (negative nodes_to_add)"""
    result = calculate_spot_ondemand_mix(
        current_nodes=10,
        desired_nodes=5,
        existing_spot_count=7,
        existing_ondemand_count=3
    )
    
    assert result['spot'] == 0
    assert result['ondemand'] == 0


@patch('spot_instance_helper.boto3')
def test_get_spot_interruption_notices(mock_boto):
    """Test detection of spot interruption notices"""
    mock_ec2 = MagicMock()
    mock_boto.client.return_value = mock_ec2
    
    mock_ec2.describe_instances.return_value = {
        'Reservations': [
            {
                'Instances': [
                    {
                        'InstanceId': 'i-interrupted',
                        'Tags': [
                            {'Key': 'Cluster', 'Value': 'test-cluster'},
                            {'Key': 'SpotInterruption', 'Value': 'true'}
                        ]
                    },
                    {
                        'InstanceId': 'i-normal',
                        'Tags': [
                            {'Key': 'Cluster', 'Value': 'test-cluster'}
                        ]
                    }
                ]
            }
        ]
    }
    
    interrupted = get_spot_interruption_notices('test-cluster')
    
    assert len(interrupted) == 1
    assert 'i-interrupted' in interrupted


@patch('spot_instance_helper.boto3')
def test_handle_spot_interruption(mock_boto):
    """Test spot interruption handling"""
    mock_ec2 = MagicMock()
    mock_boto.client.return_value = mock_ec2
    
    result = handle_spot_interruption('i-spot-123', 'test-cluster')
    
    assert result is True
    mock_ec2.create_tags.assert_called_once()
    
    # Verify tags were set
    call_args = mock_ec2.create_tags.call_args
    assert call_args[1]['Resources'] == ['i-spot-123']
    tags = {tag['Key']: tag['Value'] for tag in call_args[1]['Tags']}
    assert tags['SpotInterruptionHandled'] == 'true'
    assert 'InterruptionTime' in tags


@patch('spot_instance_helper.boto3')
def test_get_spot_price_recommendations(mock_boto):
    """Test spot price fetching"""
    mock_ec2 = MagicMock()
    mock_boto.client.return_value = mock_ec2
    
    mock_ec2.describe_spot_price_history.return_value = {
        'SpotPriceHistory': [
            {
                'AvailabilityZone': 'ap-southeast-1a',
                'SpotPrice': '0.0312',
                'InstanceType': 't3.medium'
            },
            {
                'AvailabilityZone': 'ap-southeast-1b',
                'SpotPrice': '0.0298',
                'InstanceType': 't3.medium'
            }
        ]
    }
    
    prices = get_spot_price_recommendations(
        instance_type='t3.medium',
        availability_zones=['ap-southeast-1a', 'ap-southeast-1b']
    )
    
    assert len(prices) == 2
    assert prices['ap-southeast-1a'] == 0.0312
    assert prices['ap-southeast-1b'] == 0.0298


def test_should_use_spot_instance_below_target():
    """Test spot decision when below target ratio"""
    # Current: 5 spot out of 10 nodes (50% < 70%)
    result = should_use_spot_instance(
        current_spot_count=5,
        total_nodes=10,
        target_ratio=0.70
    )
    
    assert result is True


def test_should_use_spot_instance_above_target():
    """Test spot decision when above target ratio"""
    # Current: 8 spot out of 10 nodes (80% > 70%)
    result = should_use_spot_instance(
        current_spot_count=8,
        total_nodes=10,
        target_ratio=0.70
    )
    
    assert result is False


def test_should_use_spot_instance_at_target():
    """Test spot decision when exactly at target ratio"""
    # Current: 7 spot out of 10 nodes (70% == 70%)
    result = should_use_spot_instance(
        current_spot_count=7,
        total_nodes=10,
        target_ratio=0.70
    )
    
    assert result is False  # At target, use on-demand


def test_should_use_spot_instance_empty_cluster():
    """Test spot decision for first node"""
    result = should_use_spot_instance(
        current_spot_count=0,
        total_nodes=0,
        target_ratio=0.70
    )
    
    assert result is True  # Start with spot


@patch('spot_instance_helper.boto3')
def test_get_spot_interruption_notices_empty(mock_boto):
    """Test when no interruptions are present"""
    mock_ec2 = MagicMock()
    mock_boto.client.return_value = mock_ec2
    
    mock_ec2.describe_instances.return_value = {
        'Reservations': []
    }
    
    interrupted = get_spot_interruption_notices('test-cluster')
    
    assert len(interrupted) == 0


@patch('spot_instance_helper.boto3')
def test_handle_spot_interruption_failure(mock_boto):
    """Test spot interruption handling with boto3 error"""
    mock_ec2 = MagicMock()
    mock_boto.client.return_value = mock_ec2
    
    mock_ec2.create_tags.side_effect = Exception("EC2 API error")
    
    result = handle_spot_interruption('i-spot-123', 'test-cluster')
    
    assert result is False
