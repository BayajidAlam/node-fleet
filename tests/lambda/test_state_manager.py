"""
Unit tests for state_manager module
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock
from botocore.exceptions import ClientError

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("state_manager", os.path.join(os.path.dirname(__file__), '../../lambda/state_manager.py'))
state_manager_module = importlib.util.module_from_spec(spec)

# Register in sys.modules so patch('state_manager.boto3') works
sys.modules['state_manager'] = state_manager_module
spec.loader.exec_module(state_manager_module)
StateManager = state_manager_module.StateManager


@pytest.fixture
def mock_dynamodb():
    """Mock DynamoDB client"""
    with patch('state_manager.boto3') as mock_boto:
        mock_resource = MagicMock()
        mock_table = MagicMock()
        mock_boto.resource.return_value = mock_resource
        mock_resource.Table.return_value = mock_table
        yield mock_table


def test_acquire_lock_success(mock_dynamodb):
    """Test successful lock acquisition"""
    mock_dynamodb.update_item.return_value = {}
    
    manager = StateManager("test-table", "cluster-1")
    result = manager.acquire_lock()
    
    assert result is True
    mock_dynamodb.update_item.assert_called_once()


def test_acquire_lock_already_held(mock_dynamodb):
    """Test lock acquisition when already held"""
    error = ClientError(
        {'Error': {'Code': 'ConditionalCheckFailedException'}},
        'UpdateItem'
    )
    mock_dynamodb.update_item.side_effect = error
    
    manager = StateManager("test-table", "cluster-1")
    result = manager.acquire_lock()
    
    assert result is False


def test_release_lock(mock_dynamodb):
    """Test lock release"""
    mock_dynamodb.update_item.return_value = {}
    
    manager = StateManager("test-table", "cluster-1")
    manager.release_lock()
    
    mock_dynamodb.update_item.assert_called_once()


def test_get_state_exists(mock_dynamodb):
    """Test getting existing state"""
    mock_dynamodb.get_item.return_value = {
        'Item': {
            'cluster_id': 'cluster-1',
            'node_count': 5,
            'last_scale_time': 1234567890,
            'scaling_in_progress': False
        }
    }
    
    manager = StateManager("test-table", "cluster-1")
    state = manager.get_state()
    
    assert state['node_count'] == 5
    assert state['last_scale_time'] == 1234567890


def test_get_state_not_exists(mock_dynamodb):
    """Test getting state when item doesn't exist"""
    mock_dynamodb.get_item.return_value = {}
    
    manager = StateManager("test-table", "cluster-1")
    state = manager.get_state()
    
    assert state['cluster_id'] == 'cluster-1'
    assert state['node_count'] == 2  # Default
    assert state['last_scale_time'] == 0
    assert state['scaling_in_progress'] is False


def test_update_state(mock_dynamodb):
    """Test state update"""
    mock_dynamodb.update_item.return_value = {}

    manager = StateManager("test-table", "cluster-1")
    manager.update_state(7)

    mock_dynamodb.update_item.assert_called_once()
    call_args = mock_dynamodb.update_item.call_args
    assert call_args[1]['ExpressionAttributeValues'][':count'] == 7


def test_lock_expiry_is_360_seconds(mock_dynamodb):
    """Lock expiry must be 360s (not 120s, not 300s)"""
    import time as time_mod
    mock_dynamodb.update_item.return_value = {}

    manager = StateManager("test-table", "cluster-1")
    before = int(time_mod.time())
    manager.acquire_lock()
    after = int(time_mod.time())

    call_args = mock_dynamodb.update_item.call_args
    expiry = call_args[1]['ExpressionAttributeValues'][':expiry']
    # expiry must be approximately now + 360
    assert before + 360 <= expiry <= after + 360


def test_stale_lock_auto_cleared(mock_dynamodb):
    """Stale lock (age > 360s) is force-released and re-acquired"""
    import time as time_mod
    from botocore.exceptions import ClientError

    # First call: conditional check fails (lock held)
    cce = ClientError({'Error': {'Code': 'ConditionalCheckFailedException'}}, 'UpdateItem')

    # get_state returns a lock that is 400s old (stale)
    mock_dynamodb.get_item.return_value = {
        'Item': {
            'cluster_id': 'cluster-1',
            'node_count': 3,
            'last_scale_time': 0,
            'scaling_in_progress': True,
            'lock_acquired_at': int(time_mod.time()) - 400,
            'metrics_history': []
        }
    }
    # First update_item fails (lock held), second and third succeed (release + reacquire)
    mock_dynamodb.update_item.side_effect = [cce, None, None]

    manager = StateManager("test-table", "cluster-1")
    result = manager.acquire_lock()

    assert result is True
    assert mock_dynamodb.update_item.call_count == 3


def test_store_drain_state(mock_dynamodb):
    """Drain state persisted to DynamoDB with correct keys"""
    mock_dynamodb.update_item.return_value = {}

    manager = StateManager("test-table", "cluster-1")
    manager.store_drain_state([{
        'instance_id': 'i-abc',
        'node_name': 'worker.internal',
        'command_id': 'cmd-xyz',
        'master_instance_id': 'i-master',
        'start_time': 1700000000
    }])

    mock_dynamodb.update_item.assert_called_once()
    call_args = mock_dynamodb.update_item.call_args
    items = call_args[1]['ExpressionAttributeValues'][':items']
    assert items[0]['instance_id'] == 'i-abc'
    assert items[0]['command_id'] == 'cmd-xyz'


def test_get_pending_drains(mock_dynamodb):
    """get_pending_drains returns typed list from DynamoDB state"""
    from decimal import Decimal
    mock_dynamodb.get_item.return_value = {
        'Item': {
            'cluster_id': 'cluster-1',
            'node_count': 3,
            'last_scale_time': 0,
            'scaling_in_progress': False,
            'draining_instances': [{
                'instance_id': 'i-drain1',
                'node_name': 'worker.internal',
                'command_id': 'cmd-1',
                'master_instance_id': 'i-master',
                'start_time': Decimal('1700000000')
            }],
            'metrics_history': []
        }
    }

    manager = StateManager("test-table", "cluster-1")
    drains = manager.get_pending_drains()

    assert len(drains) == 1
    assert drains[0]['instance_id'] == 'i-drain1'
    assert drains[0]['start_time'] == 1700000000


def test_clear_drain_instance(mock_dynamodb):
    """clear_drain_instance removes only the specified instance"""
    from decimal import Decimal
    mock_dynamodb.get_item.return_value = {
        'Item': {
            'cluster_id': 'cluster-1',
            'node_count': 3,
            'last_scale_time': 0,
            'scaling_in_progress': False,
            'draining_instances': [
                {'instance_id': 'i-keep', 'node_name': 'n1', 'command_id': 'c1', 'master_instance_id': 'i-m', 'start_time': Decimal('100')},
                {'instance_id': 'i-remove', 'node_name': 'n2', 'command_id': 'c2', 'master_instance_id': 'i-m', 'start_time': Decimal('200')},
            ],
            'metrics_history': []
        }
    }
    mock_dynamodb.update_item.return_value = {}

    manager = StateManager("test-table", "cluster-1")
    manager.clear_drain_instance('i-remove')

    call_args = mock_dynamodb.update_item.call_args
    remaining = call_args[1]['ExpressionAttributeValues'][':items']
    assert len(remaining) == 1
    assert remaining[0]['instance_id'] == 'i-keep'


def test_store_pending_scale_up(mock_dynamodb):
    """Pending scale-up instances stored correctly"""
    mock_dynamodb.update_item.return_value = {}

    manager = StateManager("test-table", "cluster-1")
    manager.store_pending_scale_up(['i-new1', 'i-new2'], 1700000000)

    mock_dynamodb.update_item.assert_called_once()
    call_args = mock_dynamodb.update_item.call_args
    items = call_args[1]['ExpressionAttributeValues'][':items']
    assert len(items) == 2
    assert items[0]['instance_id'] == 'i-new1'
    assert items[1]['instance_id'] == 'i-new2'


def test_get_pending_scale_ups(mock_dynamodb):
    """get_pending_scale_ups returns typed list"""
    from decimal import Decimal
    mock_dynamodb.get_item.return_value = {
        'Item': {
            'cluster_id': 'cluster-1',
            'node_count': 3,
            'last_scale_time': 0,
            'scaling_in_progress': False,
            'pending_scale_up': [
                {'instance_id': 'i-pending', 'launch_time': Decimal('1700000000')}
            ],
            'metrics_history': []
        }
    }

    manager = StateManager("test-table", "cluster-1")
    pending = manager.get_pending_scale_ups()

    assert len(pending) == 1
    assert pending[0]['instance_id'] == 'i-pending'
    assert pending[0]['launch_time'] == 1700000000


def test_update_state_stores_last_scale_time(mock_dynamodb):
    """update_state writes last_scale_time (not zero)"""
    import time as time_mod
    mock_dynamodb.update_item.return_value = {}

    manager = StateManager("test-table", "cluster-1")
    before = int(time_mod.time())
    manager.update_state(5)
    after = int(time_mod.time())

    call_args = mock_dynamodb.update_item.call_args
    ts = call_args[1]['ExpressionAttributeValues'][':time']
    assert before <= ts <= after


def test_get_pending_drains_empty(mock_dynamodb):
    """No draining instances returns empty list"""
    mock_dynamodb.get_item.return_value = {
        'Item': {
            'cluster_id': 'cluster-1',
            'node_count': 3,
            'last_scale_time': 0,
            'scaling_in_progress': False,
            'metrics_history': []
        }
    }

    manager = StateManager("test-table", "cluster-1")
    drains = manager.get_pending_drains()

    assert drains == []
