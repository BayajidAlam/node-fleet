"""
Unit tests for state_manager module
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from botocore.exceptions import ClientError
from lambda.state_manager import StateManager


@pytest.fixture
def mock_dynamodb():
    """Mock DynamoDB client"""
    with patch('lambda.state_manager.boto3') as mock_boto:
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
