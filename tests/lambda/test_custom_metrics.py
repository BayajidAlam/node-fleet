"""
Tests for custom application metrics functionality
"""

import pytest
import sys
import os
import json
from unittest.mock import Mock, MagicMock, patch

# Add parent directory and lambda directory to path to import lambda modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../lambda')))

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("custom_metrics", os.path.join(os.path.dirname(__file__), '../../lambda/custom_metrics.py'))
custom_metrics_module = importlib.util.module_from_spec(spec)

# Register in sys.modules so patch works
sys.modules['custom_metrics'] = custom_metrics_module
spec.loader.exec_module(custom_metrics_module)

CustomMetricsCollector = custom_metrics_module.CustomMetricsCollector
get_custom_metrics = custom_metrics_module.get_custom_metrics


@pytest.fixture
def mock_requests():
    """Mock requests.get for Prometheus queries"""
    with patch('custom_metrics.requests.get') as mock_get:
        yield mock_get


def test_custom_metrics_collector_init():
    """Test CustomMetricsCollector initialization"""
    collector = CustomMetricsCollector('http://localhost:9090')
    assert collector.prometheus_url == 'http://localhost:9090'


def test_get_queue_depth(mock_requests):
    """Test queue depth retrieval"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        'status': 'success',
        'data': {
            'result': [
                {'value': ['1234567890', '42']}
            ]
        }
    }
    mock_requests.return_value = mock_response
    
    collector = CustomMetricsCollector('http://localhost:9090')
    depth = collector.get_queue_depth('default')
    
    assert depth == 42
    mock_requests.assert_called_once()


def test_get_queue_depth_no_data(mock_requests):
    """Test queue depth when no data available"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        'status': 'success',
        'data': {'result': []}
    }
    mock_requests.return_value = mock_response
    
    collector = CustomMetricsCollector('http://localhost:9090')
    depth = collector.get_queue_depth('default')
    
    assert depth is None


def test_get_api_latency_p95(mock_requests):
    """Test P95 latency retrieval"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        'status': 'success',
        'data': {
            'result': [
                {'value': ['1234567890', '250.0']}  # 250.0ms (already scaled by * 1000 in query)
            ]
        }
    }
    mock_requests.return_value = mock_response
    
    collector = CustomMetricsCollector('http://localhost:9090')
    latency = collector.get_api_latency_p95('api')
    
    assert latency == 250.0
    assert 'histogram_quantile' in mock_requests.call_args[1]['params']['query']


def test_get_request_rate(mock_requests):
    """Test request rate retrieval"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        'status': 'success',
        'data': {
            'result': [
                {'value': ['1234567890', '125.5']}
            ]
        }
    }
    mock_requests.return_value = mock_response
    
    collector = CustomMetricsCollector('http://localhost:9090')
    rps = collector.get_request_rate('api')
    
    assert rps == 125.5
    assert 'rate(http_requests_total' in mock_requests.call_args[1]['params']['query']


def test_get_error_rate(mock_requests):
    """Test error rate calculation"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        'status': 'success',
        'data': {
            'result': [
                {'value': ['1234567890', '3.5']}
            ]
        }
    }
    mock_requests.return_value = mock_response
    
    collector = CustomMetricsCollector('http://localhost:9090')
    error_rate = collector.get_error_rate('api')
    
    assert error_rate == 3.5


def test_get_active_connections(mock_requests):
    """Test active connections retrieval"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        'status': 'success',
        'data': {
            'result': [
                {'value': ['1234567890', '350']}
            ]
        }
    }
    mock_requests.return_value = mock_response
    
    collector = CustomMetricsCollector('http://localhost:9090')
    connections = collector.get_active_connections('api')
    
    assert connections == 350


def test_evaluate_scaling_need_queue_depth(mock_requests):
    """Test scaling evaluation based on queue depth"""
    mock_response = MagicMock()
    # Mock sequence of responses for different queries
    mock_response.json.side_effect = [
        {'status': 'success', 'data': {'result': [{'value': ['0', '150']}]}},  # queue
        {'status': 'success', 'data': {'result': []}},  # latency
        {'status': 'success', 'data': {'result': []}},  # error
        {'status': 'success', 'data': {'result': []}}   # connections
    ]
    mock_requests.return_value = mock_response
    
    collector = CustomMetricsCollector('http://localhost:9090')
    result = collector.evaluate_scaling_need({
        'queue_depth_max': 100
    })
    
    assert result['scale_needed'] is True
    assert any('Queue depth' in r for r in result['reasons'])


def test_evaluate_scaling_need_no_scale(mock_requests):
    """Test no scaling needed when all metrics normal"""
    mock_response = MagicMock()
    mock_response.json.side_effect = [
        {'status': 'success', 'data': {'result': [{'value': ['0', '50']}]}},   # queue
        {'status': 'success', 'data': {'result': [{'value': ['0', '0.100']}]}}, # latency
        {'status': 'success', 'data': {'result': []}},                         # error
        {'status': 'success', 'data': {'result': [{'value': ['0', '500']}]}}   # connections
    ]
    mock_requests.return_value = mock_response
    
    collector = CustomMetricsCollector('http://localhost:9090')
    result = collector.evaluate_scaling_need({
        'queue_depth_max': 100,
        'latency_p95_max_ms': 500,
        'error_rate_max_percent': 5.0,
        'connections_per_node': 1000
    })
    
    assert result['scale_needed'] is False
    assert len(result['reasons']) == 0


def test_prometheus_error_handling(mock_requests):
    """Test graceful error handling for Prometheus failures"""
    mock_requests.side_effect = Exception("Prometheus unavailable")
    
    collector = CustomMetricsCollector('http://localhost:9090')
    depth = collector.get_queue_depth()
    
    assert depth is None
