"""
Unit tests for ec2_manager module
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Add lambda directory to sys.path for relative imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lambda'))

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("ec2_manager", os.path.join(os.path.dirname(__file__), '../../lambda/ec2_manager.py'))
ec2_manager_module = importlib.util.module_from_spec(spec)

# Register in sys.modules so patch('ec2_manager.boto3') works
sys.modules['ec2_manager'] = ec2_manager_module
spec.loader.exec_module(ec2_manager_module)
EC2Manager = ec2_manager_module.EC2Manager


@pytest.fixture
def mock_ec2():
    """Mock EC2 client"""
    with patch('ec2_manager.boto3') as mock_boto:
        mock_client = MagicMock()
        mock_boto.client.return_value = mock_client
        
        # Mock subnet discovery (needed for scale-up logic)
        mock_client.describe_subnets.return_value = {
            'Subnets': [
                {'SubnetId': 'subnet-az1', 'AvailabilityZone': 'ap-southeast-1a'},
                {'SubnetId': 'subnet-az2', 'AvailabilityZone': 'ap-southeast-1b'}
            ]
        }
        yield mock_client


def test_scale_up_on_demand_only(mock_ec2):
    """Test scaling up with on-demand instances"""
    mock_ec2.run_instances.return_value = {
        'Instances': [
            {'InstanceId': 'i-123'}
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


def test_complete_pending_drains_success(mock_ec2):
    """Drain succeeded — instance terminated, state cleared"""
    mock_ec2.get_command_invocation.return_value = {
        'Status': 'Success',
        'StandardOutputContent': 'node/worker-1 drained',
        'StandardErrorContent': ''
    }
    mock_ec2.terminate_instances.return_value = {}

    state_mgr = Mock()
    state_mgr.get_pending_drains.return_value = [{
        'instance_id': 'i-123',
        'node_name': 'worker-1.internal',
        'command_id': 'cmd-abc',
        'master_instance_id': 'i-master',
        'start_time': int(__import__('time').time()) - 60
    }]

    manager = EC2Manager(worker_template_id='lt-ondemand')
    terminated = manager.complete_pending_drains(state_mgr)

    assert 'i-123' in terminated
    mock_ec2.terminate_instances.assert_called_once_with(InstanceIds=['i-123'])
    state_mgr.clear_drain_instance.assert_called_once_with('i-123')


def test_complete_pending_drains_failed_status(mock_ec2):
    """Drain failed — skip termination to protect workloads"""
    mock_ec2.get_command_invocation.return_value = {
        'Status': 'Failed',
        'StandardOutputContent': 'error evicting pod',
        'StandardErrorContent': 'timeout'
    }

    state_mgr = Mock()
    state_mgr.get_pending_drains.return_value = [{
        'instance_id': 'i-456',
        'node_name': 'worker-2.internal',
        'command_id': 'cmd-def',
        'master_instance_id': 'i-master',
        'start_time': int(__import__('time').time()) - 60
    }]

    manager = EC2Manager(worker_template_id='lt-ondemand')
    terminated = manager.complete_pending_drains(state_mgr)

    assert terminated == []
    mock_ec2.terminate_instances.assert_not_called()
    state_mgr.clear_drain_instance.assert_called_once_with('i-456')


def test_complete_pending_drains_timeout_elapsed(mock_ec2):
    """Drain timeout exceeded (elapsed > 300s) — skip termination, clear state"""
    mock_ec2.get_command_invocation.return_value = {
        'Status': 'InProgress',
        'StandardOutputContent': '',
        'StandardErrorContent': ''
    }

    state_mgr = Mock()
    state_mgr.get_pending_drains.return_value = [{
        'instance_id': 'i-789',
        'node_name': 'worker-3.internal',
        'command_id': 'cmd-ghi',
        'master_instance_id': 'i-master',
        'start_time': int(__import__('time').time()) - 400  # 400s ago > 300s timeout
    }]

    manager = EC2Manager(worker_template_id='lt-ondemand')
    terminated = manager.complete_pending_drains(state_mgr)

    assert terminated == []
    mock_ec2.terminate_instances.assert_not_called()


def test_complete_pending_drains_still_in_progress(mock_ec2):
    """Drain still running — do not terminate yet"""
    mock_ec2.get_command_invocation.return_value = {
        'Status': 'InProgress',
        'StandardOutputContent': '',
        'StandardErrorContent': ''
    }

    state_mgr = Mock()
    state_mgr.get_pending_drains.return_value = [{
        'instance_id': 'i-101',
        'node_name': 'worker-4.internal',
        'command_id': 'cmd-jkl',
        'master_instance_id': 'i-master',
        'start_time': int(__import__('time').time()) - 30  # Only 30s ago
    }]

    manager = EC2Manager(worker_template_id='lt-ondemand')
    terminated = manager.complete_pending_drains(state_mgr)

    assert terminated == []
    mock_ec2.terminate_instances.assert_not_called()
    state_mgr.clear_drain_instance.assert_not_called()


def test_complete_pending_drains_empty(mock_ec2):
    """No pending drains — return empty list immediately"""
    state_mgr = Mock()
    state_mgr.get_pending_drains.return_value = []

    manager = EC2Manager(worker_template_id='lt-ondemand')
    terminated = manager.complete_pending_drains(state_mgr)

    assert terminated == []
    mock_ec2.get_command_invocation.assert_not_called()


def test_complete_pending_drains_keyword_missing(mock_ec2):
    """SSM exit 0 but 'drained' keyword absent — must NOT terminate"""
    mock_ec2.get_command_invocation.return_value = {
        'Status': 'Success',
        'StandardOutputContent': 'node cordoned',  # No "drained" keyword
        'StandardErrorContent': ''
    }

    state_mgr = Mock()
    state_mgr.get_pending_drains.return_value = [{
        'instance_id': 'i-bad',
        'node_name': 'worker-bad.internal',
        'command_id': 'cmd-bad',
        'master_instance_id': 'i-master',
        'start_time': int(__import__('time').time()) - 60
    }]

    manager = EC2Manager(worker_template_id='lt-ondemand')
    terminated = manager.complete_pending_drains(state_mgr)

    assert terminated == []
    mock_ec2.terminate_instances.assert_not_called()


def test_scale_up_no_subnets(mock_ec2):
    """Scale-up fails when no subnets available"""
    mock_ec2.describe_subnets.return_value = {'Subnets': []}

    manager = EC2Manager(worker_template_id='lt-ondemand')
    manager.available_subnets = []  # Force empty

    result = manager.scale_up(nodes_to_add=1, reason="High CPU")

    assert result['success'] is False
    assert result['instance_ids'] == []
    mock_ec2.run_instances.assert_not_called()


def test_scale_up_spot_fallback_to_ondemand(mock_ec2):
    """Spot launch fails, falls back to on-demand successfully"""
    mock_ec2.run_instances.side_effect = [
        Exception("Spot capacity unavailable"),   # Spot fails
        {'Instances': [{'InstanceId': 'i-od1'}]}  # On-demand succeeds
    ]

    manager = EC2Manager(
        worker_template_id='lt-ondemand',
        worker_spot_template_id='lt-spot',
        spot_percentage=100
    )

    result = manager.scale_up(nodes_to_add=1, reason="High CPU")

    assert result['success'] is True
    assert result['instance_ids'] == ['i-od1']
    assert result['ondemand_count'] == 1
    assert result['spot_count'] == 0


def test_scale_up_all_instances_fail(mock_ec2):
    """All launch attempts fail — return failure"""
    mock_ec2.run_instances.side_effect = Exception("EC2 API error")

    manager = EC2Manager(
        worker_template_id='lt-ondemand',
        worker_spot_template_id='lt-spot',
        spot_percentage=0
    )

    result = manager.scale_up(nodes_to_add=2, reason="High CPU")

    assert result['success'] is False
    assert result['instance_ids'] == []


def test_scale_down_no_workers(mock_ec2):
    """No workers to remove — return failure"""
    mock_ec2.describe_instances.return_value = {'Reservations': []}

    manager = EC2Manager(worker_template_id='lt-ondemand')
    result = manager.scale_down(nodes_to_remove=1, reason="Low CPU")

    assert result['success'] is False
    assert result['instance_ids'] == []


def test_scale_down_critical_pod_skip(mock_ec2):
    """Drain skipped for all targets when critical pods found — no instances drained"""
    mock_ec2.describe_instances.return_value = {
        'Reservations': [{'Instances': [
            {'InstanceId': 'i-111', 'PrivateDnsName': 'worker-1.internal', 'InstanceLifecycle': 'spot'}
        ]}]
    }
    # SSM returns CRITICAL_PODS_FOUND for the check command
    mock_ec2.send_command.return_value = {'Command': {'CommandId': 'cmd-check'}}
    mock_ec2.get_command_invocation.return_value = {
        'Status': 'Success',
        'StandardOutputContent': 'CRITICAL_PODS_FOUND:statefulset=1,kube-system-non-ds=0,single-replica-deployments=0',
        'StandardErrorContent': ''
    }

    manager = EC2Manager(worker_template_id='lt-ondemand')
    result = manager.scale_down(nodes_to_remove=1, reason="Low CPU")

    assert result['success'] is False
    assert result['instance_ids'] == []


def test_check_critical_pods_statefulset(mock_ec2):
    """StatefulSet pod on node — returns has_critical=True"""
    mock_ec2.send_command.return_value = {'Command': {'CommandId': 'cmd-1'}}
    mock_ec2.get_command_invocation.return_value = {
        'Status': 'Success',
        'StandardOutputContent': 'CRITICAL_PODS_FOUND:statefulset=1,kube-system-non-ds=0,single-replica-deployments=0',
        'StandardErrorContent': ''
    }

    manager = EC2Manager(worker_template_id='lt-ondemand')
    has_critical, reason = manager._check_critical_pods('i-master', 'worker.internal')

    assert has_critical is True
    assert 'CRITICAL_PODS_FOUND' in reason


def test_check_critical_pods_safe(mock_ec2):
    """No critical pods — returns has_critical=False"""
    mock_ec2.send_command.return_value = {'Command': {'CommandId': 'cmd-2'}}
    mock_ec2.get_command_invocation.return_value = {
        'Status': 'Success',
        'StandardOutputContent': 'SAFE_TO_DRAIN',
        'StandardErrorContent': ''
    }

    manager = EC2Manager(worker_template_id='lt-ondemand')
    has_critical, reason = manager._check_critical_pods('i-master', 'worker.internal')

    assert has_critical is False
    assert reason == ''


def test_check_critical_pods_single_replica(mock_ec2):
    """Single-replica deployment — returns has_critical=True"""
    mock_ec2.send_command.return_value = {'Command': {'CommandId': 'cmd-3'}}
    mock_ec2.get_command_invocation.return_value = {
        'Status': 'Success',
        'StandardOutputContent': 'CRITICAL_PODS_FOUND:statefulset=0,kube-system-non-ds=0,single-replica-deployments=2',
        'StandardErrorContent': ''
    }

    manager = EC2Manager(worker_template_id='lt-ondemand')
    has_critical, reason = manager._check_critical_pods('i-master', 'worker.internal')

    assert has_critical is True
    assert 'single-replica' in reason


def test_check_critical_pods_error_failsafe(mock_ec2):
    """SSM send_command raises — fail-safe returns has_critical=True"""
    mock_ec2.send_command.side_effect = Exception("SSM unavailable")

    manager = EC2Manager(worker_template_id='lt-ondemand')
    has_critical, reason = manager._check_critical_pods('i-master', 'worker.internal')

    assert has_critical is True
    assert 'check_error' in reason


def test_get_master_instance_id_not_found(mock_ec2):
    """Master not found via tag — raises RuntimeError"""
    mock_ec2.describe_instances.return_value = {'Reservations': []}

    manager = EC2Manager(worker_template_id='lt-ondemand')

    with pytest.raises(RuntimeError, match="k3s-master"):
        manager._get_master_instance_id()


def test_check_pending_scale_ups_confirmed(mock_ec2):
    """Instance running — confirmed and cleared from pending"""
    mock_ec2.describe_instances.return_value = {
        'Reservations': [{'Instances': [{'State': {'Name': 'running'}, 'InstanceId': 'i-new1'}]}]
    }

    state_mgr = Mock()
    state_mgr.get_pending_scale_ups.return_value = [{
        'instance_id': 'i-new1',
        'launch_time': int(__import__('time').time()) - 120
    }]

    manager = EC2Manager(worker_template_id='lt-ondemand')
    confirmed = manager.check_pending_scale_ups(state_mgr)

    assert 'i-new1' in confirmed
    state_mgr.clear_pending_scale_up.assert_called_once_with('i-new1')


def test_check_pending_scale_ups_timeout(mock_ec2):
    """Instance still pending after timeout — removed from tracking"""
    mock_ec2.describe_instances.return_value = {
        'Reservations': [{'Instances': [{'State': {'Name': 'pending'}, 'InstanceId': 'i-slow'}]}]
    }

    state_mgr = Mock()
    state_mgr.get_pending_scale_ups.return_value = [{
        'instance_id': 'i-slow',
        'launch_time': int(__import__('time').time()) - 700  # Exceeds 600s timeout
    }]

    manager = EC2Manager(worker_template_id='lt-ondemand')
    confirmed = manager.check_pending_scale_ups(state_mgr)

    assert confirmed == []
    state_mgr.clear_pending_scale_up.assert_called_once_with('i-slow')


def test_handle_spot_interruption_success(mock_ec2):
    """Spot interruption handled — drain initiated"""
    mock_ec2.describe_instances.return_value = {
        'Reservations': [{'Instances': [{'InstanceId': 'i-spot', 'PrivateDnsName': 'spot.internal'}]}]
    }
    mock_ec2.describe_subnets.return_value = {'Subnets': [{'SubnetId': 'subnet-1', 'AvailabilityZone': 'ap-southeast-1a'}]}
    # Master lookup for drain
    mock_ec2.describe_instances.side_effect = [
        {'Reservations': [{'Instances': [{'InstanceId': 'i-spot', 'PrivateDnsName': 'spot.internal'}]}]},
        {'Reservations': [{'Instances': [{'InstanceId': 'i-master'}]}]}
    ]
    mock_ec2.send_command.return_value = {'Command': {'CommandId': 'cmd-drain'}}

    manager = EC2Manager(worker_template_id='lt-ondemand')
    result = manager.handle_spot_interruption_event('i-spot')

    assert result['success'] is True
    assert result['action'] == 'drain_initiated'
    mock_ec2.create_tags.assert_called_once()
