"""
Integration test for complete autoscaler workflow
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Add lambda directory to path to allow importing autoscaler directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../lambda')))

# Import using importlib to avoid 'lambda' reserved keyword issue
import importlib.util
spec = importlib.util.spec_from_file_location("autoscaler", os.path.join(os.path.dirname(__file__), '../../lambda/autoscaler.py'))
autoscaler = importlib.util.module_from_spec(spec)
sys.modules['autoscaler'] = autoscaler
spec.loader.exec_module(autoscaler)

@pytest.fixture
def mock_env(monkeypatch):
    """Set environment variables and patch module globals"""
    env_vars = {
        "CLUSTER_ID": "test-cluster",
        "PROMETHEUS_URL": "http://localhost:9090",
        "STATE_TABLE": "test-state-table",
        "MIN_NODES": "2",
        "MAX_NODES": "10",
        "WORKER_LAUNCH_TEMPLATE_ID": "lt-test",
        "WORKER_SPOT_TEMPLATE_ID": "lt-spot-test",
        "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123:test"
    }
    
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
        if hasattr(autoscaler, key):
            monkeypatch.setattr(autoscaler, key, value)
            
    # Also patch integer configs
    monkeypatch.setattr(autoscaler, 'MIN_NODES', 2)
    monkeypatch.setattr(autoscaler, 'MAX_NODES', 10)
    monkeypatch.setattr(autoscaler, 'SPOT_PERCENTAGE', 70)


@patch('autoscaler.send_notification')
@patch('autoscaler.get_prometheus_credentials', return_value=('user', 'pass'))
@patch('autoscaler.EC2Manager')
@patch('autoscaler.StateManager')
@patch('autoscaler.collect_metrics')
def test_lambda_handler_scale_up(mock_metrics, mock_state_mgr, mock_ec2, mock_creds, mock_notify, mock_env):
    """Test full scale-up workflow"""
    # Mock metrics showing high CPU
    mock_metrics.return_value = {
        'cpu_usage': 75.0,
        'memory_usage': 50.0,
        'pending_pods': 2,
        'node_count': 3
    }
    
    # Mock state manager - provide metrics history showing sustained high load
    state_instance = MagicMock()
    state_instance.acquire_lock.return_value = True
    state_instance.get_state.return_value = {
        'node_count': 3,
        'last_scale_time': 0,
        'metrics_history': [
            {'timestamp': 1640000000, 'cpu_usage': 75.0, 'memory_usage': 50.0, 'pending_pods': 2},
            {'timestamp': 1640000120, 'cpu_usage': 76.0, 'memory_usage': 52.0, 'pending_pods': 2}
        ]
    }
    mock_state_mgr.return_value = state_instance
    
    # Mock EC2 manager — Step 0 returns empty lists (no pending ops)
    ec2_instance = MagicMock()
    ec2_instance.complete_pending_drains.return_value = []
    ec2_instance.check_pending_scale_ups.return_value = []
    ec2_instance.scale_up.return_value = {
        'success': True,
        'instance_ids': ['i-new1', 'i-new2'],
        'spot_count': 1,
        'ondemand_count': 1,
        'node_join_latency_ms': 5000
    }
    mock_ec2.return_value = ec2_instance
    
    # Execute
    result = autoscaler.lambda_handler({}, {})
    
    # Verify
    assert result['statusCode'] == 200
    assert 'Scaling completed' in result['body']
    ec2_instance.scale_up.assert_called_once()
    state_instance.update_state.assert_called_once()
    state_instance.release_lock.assert_called_once()
    assert mock_notify.call_count >= 1


@patch('autoscaler.send_notification')
@patch('autoscaler.send_notification')
@patch('autoscaler.get_prometheus_credentials', return_value=('user', 'pass'))
@patch('autoscaler.EC2Manager')
@patch('autoscaler.StateManager')
@patch('autoscaler.collect_metrics')
def test_lambda_handler_no_scaling_needed(mock_metrics, mock_state_mgr, mock_ec2, mock_creds, mock_notify, mock_env):
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
        'last_scale_time': 0,
        'metrics_history': []
    }
    mock_state_mgr.return_value = state_instance
    
    # Mock EC2 manager — Step 0 returns empty lists (no pending ops)
    ec2_instance = MagicMock()
    ec2_instance.complete_pending_drains.return_value = []
    ec2_instance.check_pending_scale_ups.return_value = []
    mock_ec2.return_value = ec2_instance
    
    # Execute
    result = autoscaler.lambda_handler({}, {})
    
    # Verify
    assert result['statusCode'] == 200
    assert 'No scaling needed' in result['body']
    state_instance.release_lock.assert_called_once()


@patch('autoscaler.send_notification')
@patch('autoscaler.get_prometheus_credentials', return_value=('user', 'pass'))
@patch('autoscaler.EC2Manager')
@patch('autoscaler.StateManager')
@patch('autoscaler.collect_metrics')
def test_lambda_handler_lock_held(mock_metrics, mock_state_mgr, mock_ec2, mock_creds, mock_notify, mock_env):
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
    
    # Mock EC2 manager — Step 0 returns empty lists (no pending ops)
    ec2_instance = MagicMock()
    ec2_instance.complete_pending_drains.return_value = []
    ec2_instance.check_pending_scale_ups.return_value = []
    mock_ec2.return_value = ec2_instance
    
    # Execute
    result = autoscaler.lambda_handler({}, {})
    
    # Verify
    assert result['statusCode'] == 200
    assert 'Skipped' in result['body']
    assert 'in progress' in result['body']


@patch('autoscaler.send_notification')
@patch('autoscaler.get_prometheus_credentials', return_value=('user', 'pass'))
@patch('autoscaler.EC2Manager')
@patch('autoscaler.StateManager')
@patch('autoscaler.collect_metrics')
def test_lambda_handler_error_handling(mock_metrics, mock_state_mgr, mock_ec2, mock_creds, mock_notify, mock_env):
    """Test error handling in Lambda"""
    mock_metrics.side_effect = Exception("Prometheus connection failed")

    ec2_instance = MagicMock()
    ec2_instance.complete_pending_drains.return_value = []
    ec2_instance.check_pending_scale_ups.return_value = []
    mock_ec2.return_value = ec2_instance

    with pytest.raises(Exception, match="Prometheus connection failed"):
        autoscaler.lambda_handler({}, {})

    mock_notify.assert_called_once()
    assert "Error" in mock_notify.call_args[0][0]


@patch('autoscaler.send_notification')
@patch('autoscaler.get_prometheus_credentials', return_value=('user', 'pass'))
@patch('autoscaler.EC2Manager')
@patch('autoscaler.StateManager')
@patch('autoscaler.collect_metrics')
def test_lambda_handler_scale_down(mock_metrics, mock_state_mgr, mock_ec2, mock_creds, mock_notify, mock_env):
    """Test full scale-down workflow"""
    mock_metrics.return_value = {
        'cpu_usage': 25.0,
        'memory_usage': 40.0,
        'pending_pods': 0,
        'node_count': 5
    }

    state_instance = MagicMock()
    state_instance.acquire_lock.return_value = True
    state_instance.get_state.return_value = {
        'node_count': 5,
        'last_scale_time': 0,
        'metrics_history': [
            {'timestamp': t, 'cpu_usage': 25.0, 'memory_usage': 40.0, 'pending_pods': 0}
            for t in range(4)
        ]
    }
    mock_state_mgr.return_value = state_instance

    ec2_instance = MagicMock()
    ec2_instance.complete_pending_drains.return_value = []
    ec2_instance.check_pending_scale_ups.return_value = []
    ec2_instance.scale_down.return_value = {'success': True, 'instance_ids': ['i-old1']}
    mock_ec2.return_value = ec2_instance

    result = autoscaler.lambda_handler({}, {})

    assert result['statusCode'] == 200
    ec2_instance.scale_down.assert_called_once()
    state_instance.release_lock.assert_called_once()


@patch('autoscaler.send_notification')
@patch('autoscaler.get_prometheus_credentials', return_value=('user', 'pass'))
@patch('autoscaler.EC2Manager')
@patch('autoscaler.StateManager')
@patch('autoscaler.collect_metrics')
def test_lock_always_released_on_exception(mock_metrics, mock_state_mgr, mock_ec2, mock_creds, mock_notify, mock_env):
    """Lock released in finally even when scale_up raises"""
    mock_metrics.return_value = {
        'cpu_usage': 75.0, 'memory_usage': 50.0, 'pending_pods': 2, 'node_count': 3
    }

    state_instance = MagicMock()
    state_instance.acquire_lock.return_value = True
    state_instance.get_state.return_value = {
        'node_count': 3, 'last_scale_time': 0,
        'metrics_history': [
            {'timestamp': t, 'cpu_usage': 75.0, 'memory_usage': 50.0, 'pending_pods': 2}
            for t in range(2)
        ]
    }
    mock_state_mgr.return_value = state_instance

    ec2_instance = MagicMock()
    ec2_instance.complete_pending_drains.return_value = []
    ec2_instance.check_pending_scale_ups.return_value = []
    ec2_instance.scale_up.side_effect = Exception("EC2 API failure")
    mock_ec2.return_value = ec2_instance

    with pytest.raises(Exception, match="EC2 API failure"):
        autoscaler.lambda_handler({}, {})

    state_instance.release_lock.assert_called_once()


@patch('autoscaler.send_notification')
@patch('autoscaler.EC2Manager')
@patch('autoscaler.StateManager')
@patch('autoscaler.collect_metrics')
def test_prometheus_credentials_env_vars_fallback(mock_metrics, mock_state_mgr, mock_ec2, mock_notify, mock_env, monkeypatch):
    """Credentials loaded from env vars when Secrets Manager fails"""
    monkeypatch.setenv('PROMETHEUS_USERNAME', 'env-user')
    monkeypatch.setenv('PROMETHEUS_PASSWORD', 'env-pass')

    import boto3
    with patch('autoscaler.boto3') as mock_b3:
        sm = MagicMock()
        sm.get_secret_value.side_effect = Exception("Secrets Manager unavailable")
        mock_b3.client.return_value = sm

        user, pwd = autoscaler.get_prometheus_credentials()

    assert user == 'env-user'
    assert pwd == 'env-pass'


def test_prometheus_credentials_missing_raises(mock_env, monkeypatch):
    """Missing credentials raise ValueError — fail-fast, never hardcoded fallback"""
    monkeypatch.delenv('PROMETHEUS_USERNAME', raising=False)
    monkeypatch.delenv('PROMETHEUS_PASSWORD', raising=False)

    with patch('autoscaler.boto3') as mock_b3:
        sm = MagicMock()
        sm.get_secret_value.side_effect = Exception("unavailable")
        mock_b3.client.return_value = sm

        with pytest.raises(ValueError, match="Prometheus credentials"):
            autoscaler.get_prometheus_credentials()


@patch('autoscaler.send_notification')
@patch('autoscaler.get_prometheus_credentials', return_value=('user', 'pass'))
@patch('autoscaler.EC2Manager')
@patch('autoscaler.StateManager')
@patch('autoscaler.collect_metrics')
def test_pending_drain_completed_step0(mock_metrics, mock_state_mgr, mock_ec2, mock_creds, mock_notify, mock_env):
    """Step 0: pending drain from previous invocation completed → notification sent"""
    mock_metrics.return_value = {
        'cpu_usage': 50.0, 'memory_usage': 50.0, 'pending_pods': 0, 'node_count': 3
    }

    state_instance = MagicMock()
    state_instance.acquire_lock.return_value = True
    state_instance.get_state.return_value = {'node_count': 3, 'last_scale_time': 0, 'metrics_history': []}
    mock_state_mgr.return_value = state_instance

    ec2_instance = MagicMock()
    ec2_instance.complete_pending_drains.return_value = ['i-terminated1']  # 1 drain completed
    ec2_instance.check_pending_scale_ups.return_value = []
    mock_ec2.return_value = ec2_instance

    result = autoscaler.lambda_handler({}, {})

    assert result['statusCode'] == 200
    # Notification sent for completed drain
    assert mock_notify.call_count >= 1
    drain_notif = mock_notify.call_args_list[0][0][0]
    assert 'i-terminated1' in drain_notif


@patch('autoscaler.send_notification')
@patch('autoscaler.get_prometheus_credentials', return_value=('user', 'pass'))
@patch('autoscaler.EC2Manager')
@patch('autoscaler.StateManager')
@patch('autoscaler.collect_metrics')
def test_spot_interruption_event_handled(mock_metrics, mock_state_mgr, mock_ec2, mock_creds, mock_notify, mock_env):
    """Spot interruption event routes to handle_spot_interruption_event"""
    event = {
        'detail-type': 'EC2 Spot Instance Interruption Warning',
        'detail': {'instance-id': 'i-spot123'}
    }

    ec2_instance = MagicMock()
    ec2_instance.complete_pending_drains.return_value = []
    ec2_instance.check_pending_scale_ups.return_value = []
    ec2_instance.handle_spot_interruption_event.return_value = {
        'success': True, 'action': 'drain_initiated', 'node': 'spot.internal', 'reason': 'Spot warning'
    }
    mock_ec2.return_value = ec2_instance

    result = autoscaler.lambda_handler(event, {})

    assert result['statusCode'] == 200
    ec2_instance.handle_spot_interruption_event.assert_called_once_with('i-spot123')


@patch('autoscaler.send_notification')
@patch('autoscaler.get_prometheus_credentials', return_value=('user', 'pass'))
@patch('autoscaler.EC2Manager')
@patch('autoscaler.StateManager')
@patch('autoscaler.collect_metrics')
def test_scale_up_stores_pending_in_state(mock_metrics, mock_state_mgr, mock_ec2, mock_creds, mock_notify, mock_env):
    """After scale-up, state updated with new node count"""
    mock_metrics.return_value = {
        'cpu_usage': 75.0, 'memory_usage': 50.0, 'pending_pods': 2, 'node_count': 3
    }

    state_instance = MagicMock()
    state_instance.acquire_lock.return_value = True
    state_instance.get_state.return_value = {
        'node_count': 3, 'last_scale_time': 0,
        'metrics_history': [
            {'timestamp': t, 'cpu_usage': 75.0, 'memory_usage': 50.0, 'pending_pods': 2}
            for t in range(2)
        ]
    }
    mock_state_mgr.return_value = state_instance

    ec2_instance = MagicMock()
    ec2_instance.complete_pending_drains.return_value = []
    ec2_instance.check_pending_scale_ups.return_value = []
    ec2_instance.scale_up.return_value = {
        'success': True, 'instance_ids': ['i-x'], 'spot_count': 1, 'ondemand_count': 0
    }
    mock_ec2.return_value = ec2_instance

    autoscaler.lambda_handler({}, {})

    state_instance.update_state.assert_called_once_with(4)  # 3 + 1
