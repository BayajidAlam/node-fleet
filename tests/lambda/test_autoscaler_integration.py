"""
Integration test for complete autoscaler workflow
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("autoscaler", os.path.join(os.path.dirname(__file__), '../../lambda/autoscaler.py'))
autoscaler_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(autoscaler_module)
lambda_handler = autoscaler_module.lambda_handler


@pytest.fixture
def mock_env(monkeypatch):
    """Set environment variables"""
    monkeypatch.setenv("CLUSTER_ID", "test-cluster")
    monkeypatch.setenv("PROMETHEUS_URL", "http://localhost:9090")
    monkeypatch.setenv("STATE_TABLE", "test-state-table")
    monkeypatch.setenv("MIN_NODES", "2")
    monkeypatch.setenv("MAX_NODES", "10")
    monkeypatch.setenv("WORKER_LAUNCH_TEMPLATE_ID", "lt-test")
    monkeypatch.setenv("WORKER_SPOT_TEMPLATE_ID", "lt-spot-test")
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:test")


@patch('lambda.autoscaler.send_notification')
@patch('lambda.autoscaler.EC2Manager')
@patch('lambda.autoscaler.StateManager')
@patch('lambda.autoscaler.collect_metrics')
def test_lambda_handler_scale_up(mock_metrics, mock_state_mgr, mock_ec2, mock_notify, mock_env):
    """Test full scale-up workflow"""
    # Mock metrics showing high CPU
    mock_metrics.return_value = {
        'cpu_usage': 75.0,
        'memory_usage': 50.0,
        'pending_pods': 2,
        'node_count': 3
    }
    
    # Mock state manager
    state_instance = MagicMock()
    state_instance.acquire_lock.return_value = True
    state_instance.get_state.return_value = {
        'node_count': 3,
        'last_scale_time': 0
    }
    mock_state_mgr.return_value = state_instance
    
    # Mock EC2 manager
    ec2_instance = MagicMock()
    ec2_instance.scale_up.return_value = {
        'success': True,
        'instance_ids': ['i-new1', 'i-new2'],
        'spot_count': 1,
        'ondemand_count': 1
    }
    mock_ec2.return_value = ec2_instance
    
    # Execute
    result = lambda_handler({}, {})
    
    # Verify
    assert result['statusCode'] == 200
    assert 'Scaling completed' in result['body']
    ec2_instance.scale_up.assert_called_once()
    state_instance.update_state.assert_called_once()
    state_instance.release_lock.assert_called_once()
    mock_notify.assert_called_once()


@patch('lambda.autoscaler.send_notification')
@patch('lambda.autoscaler.StateManager')
@patch('lambda.autoscaler.collect_metrics')
def test_lambda_handler_no_scaling_needed(mock_metrics, mock_state_mgr, mock_notify, mock_env):
    """Test workflow when no scaling is needed"""
    # Mock stable metrics
    mock_metrics.return_value = {
        'cpu_usage': 50.0,
        'memory_usage': 50.0,
        'pending_pods': 0,
        'node_count': 3
    }
    
    # Mock state manager
    state_instance = MagicMock()
    state_instance.acquire_lock.return_value = True
    state_instance.get_state.return_value = {
        'node_count': 3,
        'last_scale_time': 0
    }
    mock_state_mgr.return_value = state_instance
    
    # Execute
    result = lambda_handler({}, {})
    
    # Verify
    assert result['statusCode'] == 200
    assert 'No scaling needed' in result['body']
    state_instance.release_lock.assert_called_once()


@patch('lambda.autoscaler.send_notification')
@patch('lambda.autoscaler.StateManager')
@patch('lambda.autoscaler.collect_metrics')
def test_lambda_handler_lock_held(mock_metrics, mock_state_mgr, mock_notify, mock_env):
    """Test workflow when lock is already held"""
    mock_metrics.return_value = {
        'cpu_usage': 75.0,
        'memory_usage': 50.0,
        'pending_pods': 0,
        'node_count': 3
    }
    
    # Mock state manager - lock acquisition fails
    state_instance = MagicMock()
    state_instance.acquire_lock.return_value = False
    mock_state_mgr.return_value = state_instance
    
    # Execute
    result = lambda_handler({}, {})
    
    # Verify
    assert result['statusCode'] == 200
    assert 'Skipped' in result['body']
    assert 'in progress' in result['body']


@patch('lambda.autoscaler.send_notification')
@patch('lambda.autoscaler.StateManager')
@patch('lambda.autoscaler.collect_metrics')
def test_lambda_handler_error_handling(mock_metrics, mock_state_mgr, mock_notify, mock_env):
    """Test error handling in Lambda"""
    # Mock metrics to raise error
    mock_metrics.side_effect = Exception("Prometheus connection failed")
    
    # Execute and expect exception
    with pytest.raises(Exception, match="Prometheus connection failed"):
        lambda_handler({}, {})
    
    # Verify error notification sent
    mock_notify.assert_called_once()
    assert "Error" in mock_notify.call_args[0][0]
