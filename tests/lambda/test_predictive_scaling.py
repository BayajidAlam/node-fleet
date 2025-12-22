"""
Tests for predictive scaling functionality
"""

import pytest
import sys
import os
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timedelta

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("predictive_scaling", os.path.join(os.path.dirname(__file__), '../../lambda/predictive_scaling.py'))
predictive_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(predictive_module)

PredictiveScaler = predictive_module.PredictiveScaler


@pytest.fixture
def mock_dynamodb():
    """Mock DynamoDB table"""
    with patch('predictive_scaling.boto3') as mock_boto:
        mock_resource = MagicMock()
        mock_table = MagicMock()
        mock_boto.resource.return_value = mock_resource
        mock_resource.Table.return_value = mock_table
        yield mock_table


def test_predictive_scaler_initialization():
    """Test PredictiveScaler initialization"""
    with patch('predictive_scaling.boto3'):
        scaler = PredictiveScaler('test-table', lookback_days=14)
        assert scaler.table_name == 'test-table'
        assert scaler.lookback_days == 14


def test_store_metrics(mock_dynamodb):
    """Test storing metrics to DynamoDB"""
    scaler = PredictiveScaler('test-table')
    timestamp = datetime(2025, 12, 22, 14, 30)
    
    result = scaler.store_metrics(
        timestamp=timestamp,
        cpu_percent=75.5,
        memory_percent=68.2,
        pending_pods=2,
        node_count=5
    )
    
    assert result is True
    mock_dynamodb.put_item.assert_called_once()
    
    # Verify item structure
    call_args = mock_dynamodb.put_item.call_args
    item = call_args[1]['Item']
    assert item['cpu_percent'] == '75.5'
    assert item['memory_percent'] == '68.2'
    assert item['pending_pods'] == 2
    assert item['node_count'] == 5
    assert item['hour'] == 14
    assert item['day_of_week'] == 6  # Sunday


def test_get_historical_metrics(mock_dynamodb):
    """Test retrieving historical metrics"""
    mock_dynamodb.scan.return_value = {
        'Items': [
            {
                'timestamp': '2025-12-22T10:00:00',
                'hour': 10,
                'day_of_week': 6,
                'cpu_percent': '65.0',
                'memory_percent': '55.0',
                'pending_pods': 0,
                'node_count': 4
            },
            {
                'timestamp': '2025-12-22T11:00:00',
                'hour': 11,
                'day_of_week': 6,
                'cpu_percent': '72.5',
                'memory_percent': '60.0',
                'pending_pods': 1,
                'node_count': 4
            }
        ]
    }
    
    scaler = PredictiveScaler('test-table')
    metrics = scaler.get_historical_metrics(days=7)
    
    assert len(metrics) == 2
    assert metrics[0]['cpu_percent'] == 65.0  # Converted from string
    assert metrics[1]['memory_percent'] == 60.0


def test_detect_hourly_patterns():
    """Test hourly pattern detection"""
    metrics = [
        {'hour': 9, 'cpu_percent': 50.0, 'memory_percent': 40.0, 'pending_pods': 0},
        {'hour': 9, 'cpu_percent': 55.0, 'memory_percent': 45.0, 'pending_pods': 0},
        {'hour': 14, 'cpu_percent': 80.0, 'memory_percent': 70.0, 'pending_pods': 2},
        {'hour': 14, 'cpu_percent': 85.0, 'memory_percent': 75.0, 'pending_pods': 3},
    ]
    
    with patch('predictive_scaling.boto3'):
        scaler = PredictiveScaler('test-table')
        patterns = scaler.detect_hourly_patterns(metrics)
    
    # Check 9 AM pattern (low load)
    assert 9 in patterns
    assert patterns[9]['avg_cpu'] == 52.5
    assert patterns[9]['avg_memory'] == 42.5
    assert patterns[9]['sample_count'] == 2
    
    # Check 2 PM pattern (high load)
    assert 14 in patterns
    assert patterns[14]['avg_cpu'] == 82.5
    assert patterns[14]['avg_memory'] == 72.5
    assert patterns[14]['avg_pending'] == 2.5


def test_detect_weekly_patterns():
    """Test weekly pattern detection"""
    metrics = [
        {'day_of_week': 0, 'cpu_percent': 60.0, 'memory_percent': 50.0, 'pending_pods': 0},  # Monday
        {'day_of_week': 0, 'cpu_percent': 65.0, 'memory_percent': 55.0, 'pending_pods': 1},  # Monday
        {'day_of_week': 5, 'cpu_percent': 30.0, 'memory_percent': 25.0, 'pending_pods': 0},  # Saturday
    ]
    
    with patch('predictive_scaling.boto3'):
        scaler = PredictiveScaler('test-table')
        patterns = scaler.detect_weekly_patterns(metrics)
    
    # Monday pattern (workday)
    assert 0 in patterns
    assert patterns[0]['avg_cpu'] == 62.5
    assert patterns[0]['sample_count'] == 2
    
    # Saturday pattern (weekend)
    assert 5 in patterns
    assert patterns[5]['avg_cpu'] == 30.0


def test_predict_next_hour_load_insufficient_data(mock_dynamodb):
    """Test prediction with insufficient historical data"""
    mock_dynamodb.scan.return_value = {'Items': []}
    
    scaler = PredictiveScaler('test-table')
    prediction = scaler.predict_next_hour_load({'cpu_percent': 50.0})
    
    assert prediction is None


def test_predict_next_hour_load_with_patterns(mock_dynamodb):
    """Test prediction with sufficient historical data"""
    # Create 30 data points with clear hourly pattern
    items = []
    base_time = datetime(2025, 12, 15, 0, 0)
    
    for i in range(30):
        timestamp = base_time + timedelta(hours=i)
        hour = timestamp.hour
        # Simulate higher load at hour 14 (2 PM)
        cpu = 80.0 if hour == 14 else 50.0
        
        items.append({
            'timestamp': timestamp.isoformat(),
            'hour': hour,
            'day_of_week': timestamp.weekday(),
            'cpu_percent': str(cpu),
            'memory_percent': '50.0',
            'pending_pods': 0,
            'node_count': 4
        })
    
    mock_dynamodb.scan.return_value = {'Items': items}
    
    scaler = PredictiveScaler('test-table')
    
    # Predict for when current hour is 13 (next hour will be 14 - high load hour)
    with patch('predictive_scaling.datetime') as mock_datetime:
        mock_datetime.utcnow.return_value = datetime(2025, 12, 22, 13, 30)
        mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        
        prediction = scaler.predict_next_hour_load({'cpu_percent': 50.0})
    
    assert prediction is not None
    assert prediction['predicted_cpu'] > 70  # Should predict high load
    assert prediction['confidence'] > 0


def test_should_proactive_scale_up_cpu_spike():
    """Test proactive scale-up decision for CPU spike"""
    with patch('predictive_scaling.boto3'):
        scaler = PredictiveScaler('test-table')
        
        current_metrics = {'cpu_percent': 50.0, 'memory_percent': 45.0}
        prediction = {
            'predicted_cpu': 85.0,  # Above threshold
            'predicted_memory': 60.0,
            'predicted_pending_pods': 0,
            'confidence': 0.8
        }
        
        should_scale, reason = scaler.should_proactive_scale_up(current_metrics, prediction)
        
        assert should_scale is True
        assert 'CPU spike' in reason


def test_should_proactive_scale_up_memory_spike():
    """Test proactive scale-up decision for memory spike"""
    with patch('predictive_scaling.boto3'):
        scaler = PredictiveScaler('test-table')
        
        current_metrics = {'cpu_percent': 50.0, 'memory_percent': 45.0}
        prediction = {
            'predicted_cpu': 60.0,
            'predicted_memory': 80.0,  # Above threshold
            'predicted_pending_pods': 0,
            'confidence': 0.7
        }
        
        should_scale, reason = scaler.should_proactive_scale_up(current_metrics, prediction)
        
        assert should_scale is True
        assert 'memory spike' in reason


def test_should_proactive_scale_up_low_confidence():
    """Test no scale-up with low confidence prediction"""
    with patch('predictive_scaling.boto3'):
        scaler = PredictiveScaler('test-table')
        
        current_metrics = {'cpu_percent': 50.0, 'memory_percent': 45.0}
        prediction = {
            'predicted_cpu': 85.0,
            'predicted_memory': 60.0,
            'predicted_pending_pods': 0,
            'confidence': 0.2  # Too low
        }
        
        should_scale, reason = scaler.should_proactive_scale_up(current_metrics, prediction)
        
        assert should_scale is False
        assert 'confidence' in reason


def test_should_proactive_scale_up_no_spike():
    """Test no scale-up when predicted load is normal"""
    with patch('predictive_scaling.boto3'):
        scaler = PredictiveScaler('test-table')
        
        current_metrics = {'cpu_percent': 50.0, 'memory_percent': 45.0}
        prediction = {
            'predicted_cpu': 55.0,  # Below threshold
            'predicted_memory': 50.0,
            'predicted_pending_pods': 0,
            'confidence': 0.8
        }
        
        should_scale, reason = scaler.should_proactive_scale_up(current_metrics, prediction)
        
        assert should_scale is False


def test_calculate_recommended_nodes_increase():
    """Test node recommendation calculation for scale-up"""
    with patch('predictive_scaling.boto3'):
        scaler = PredictiveScaler('test-table')
        
        prediction = {
            'predicted_cpu': 80.0,  # High predicted load
            'predicted_memory': 70.0,
            'predicted_pending_pods': 0,
            'confidence': 0.8
        }
        
        recommended = scaler.calculate_recommended_nodes(
            prediction=prediction,
            current_nodes=4,
            target_utilization=60.0
        )
        
        assert recommended > 4  # Should recommend more nodes
        assert recommended <= 7  # But not more than 3 additional


def test_calculate_recommended_nodes_no_change():
    """Test node recommendation with normal predicted load"""
    with patch('predictive_scaling.boto3'):
        scaler = PredictiveScaler('test-table')
        
        prediction = {
            'predicted_cpu': 55.0,  # Normal load
            'predicted_memory': 50.0,
            'predicted_pending_pods': 0,
            'confidence': 0.7
        }
        
        recommended = scaler.calculate_recommended_nodes(
            prediction=prediction,
            current_nodes=4,
            target_utilization=60.0
        )
        
        assert recommended == 4  # No change needed


def test_calculate_recommended_nodes_min_bound():
    """Test node recommendation respects minimum of 2 nodes"""
    with patch('predictive_scaling.boto3'):
        scaler = PredictiveScaler('test-table')
        
        prediction = {
            'predicted_cpu': 20.0,  # Very low load
            'predicted_memory': 15.0,
            'predicted_pending_pods': 0,
            'confidence': 0.9
        }
        
        recommended = scaler.calculate_recommended_nodes(
            prediction=prediction,
            current_nodes=2,
            target_utilization=60.0
        )
        
        assert recommended >= 2  # Never go below minimum
