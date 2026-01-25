"""
Tests for custom application metrics functionality
"""

import pytest
import sys
import os
from unittest.mock import Mock, MagicMock, patch

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("custom_metrics", os.path.join(os.path.dirname(__file__), '../../lambda/custom_metrics.py'))
custom_metrics_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(custom_metrics_module)

CustomMetricsCollector = custom_metrics_module.CustomMetricsCollector
get_custom_metrics = custom_metrics_module.get_custom_metrics


@pytest.fixture
def mock_prometheus():
    """Mock Prometheus connection"""
    with patch.object(custom_metrics_module, 'PrometheusConnect') as mock:
        prom_instance = MagicMock()
        mock.return_value = prom_instance
        yield prom_instance


def test_custom_metrics_collector_init():
    """Test CustomMetricsCollector initialization"""
    with patch.object(custom_metrics_module, 'PrometheusConnect'):
        collector = CustomMetricsCollector('http://localhost:9090')
        assert collector.prom is not None


def test_get_queue_depth(mock_prometheus):
    """Test queue depth retrieval"""
    mock_prometheus.custom_query.return_value = [
        {'value': ['1234567890', '42']}
    ]
    
    collector = CustomMetricsCollector('http://localhost:9090')
    depth = collector.get_queue_depth('default')
    
    assert depth == 42
    mock_prometheus.custom_query.assert_called_once()


def test_get_queue_depth_no_data(mock_prometheus):
    """Test queue depth when no data available"""
    mock_prometheus.custom_query.return_value = []
    
    collector = CustomMetricsCollector('http://localhost:9090')
    depth = collector.get_queue_depth('default')
    
    assert depth is None


def test_get_api_latency_p95(mock_prometheus):
    """Test P95 latency retrieval"""
    mock_prometheus.custom_query.return_value = [
        {'value': ['1234567890', '0.250']}  # 250ms
    ]
    
    collector = CustomMetricsCollector('http://localhost:9090')
    latency = collector.get_api_latency_p95('api')
    
    assert latency == 250.0
    assert 'histogram_quantile' in mock_prometheus.custom_query.call_args[1]['query']


def test_get_api_latency_no_data(mock_prometheus):
    """Test latency when no data available"""
    mock_prometheus.custom_query.return_value = []
    
    collector = CustomMetricsCollector('http://localhost:9090')
    latency = collector.get_api_latency_p95('api')
    
    assert latency is None


def test_get_request_rate(mock_prometheus):
    """Test request rate retrieval"""
    mock_prometheus.custom_query.return_value = [
        {'value': ['1234567890', '125.5']}
    ]
    
    collector = CustomMetricsCollector('http://localhost:9090')
    rps = collector.get_request_rate('api')
    
    assert rps == 125.5
    assert 'rate(http_requests_total' in mock_prometheus.custom_query.call_args[1]['query']


def test_get_error_rate(mock_prometheus):
    """Test error rate calculation"""
    mock_prometheus.custom_query.return_value = [
        {'value': ['1234567890', '3.5']}
    ]
    
    collector = CustomMetricsCollector('http://localhost:9090')
    error_rate = collector.get_error_rate('api')
    
    assert error_rate == 3.5
    query = mock_prometheus.custom_query.call_args[1]['query']
    assert 'status=~"5.."' in query


def test_get_error_rate_no_errors(mock_prometheus):
    """Test error rate when no errors present"""
    mock_prometheus.custom_query.return_value = []
    
    collector = CustomMetricsCollector('http://localhost:9090')
    error_rate = collector.get_error_rate('api')
    
    assert error_rate == 0.0


def test_get_active_connections(mock_prometheus):
    """Test active connections retrieval"""
    mock_prometheus.custom_query.return_value = [
        {'value': ['1234567890', '350']}
    ]
    
    collector = CustomMetricsCollector('http://localhost:9090')
    connections = collector.get_active_connections('api')
    
    assert connections == 350


def test_get_custom_metric(mock_prometheus):
    """Test generic custom metric retrieval"""
    mock_prometheus.custom_query.return_value = [
        {'value': ['1234567890', '99.9']}
    ]
    
    collector = CustomMetricsCollector('http://localhost:9090')
    value = collector.get_custom_metric('my_custom_metric', {'env': 'prod'})
    
    assert value == 99.9
    query = mock_prometheus.custom_query.call_args[1]['query']
    assert 'my_custom_metric{env="prod"}' in query


def test_evaluate_scaling_need_queue_depth(mock_prometheus):
    """Test scaling evaluation based on queue depth"""
    # Mock queue depth exceeding threshold
    mock_prometheus.custom_query.side_effect = [
        [{'value': ['1234567890', '150']}],  # queue_depth
        [],  # latency
        [],  # error_rate
        []   # connections
    ]
    
    collector = CustomMetricsCollector('http://localhost:9090')
    result = collector.evaluate_scaling_need({
        'queue_depth_max': 100
    })
    
    assert result['scale_needed'] is True
    assert any('Queue depth' in r for r in result['reasons'])
    assert result['metrics']['queue_depth'] == 150


def test_evaluate_scaling_need_latency(mock_prometheus):
    """Test scaling evaluation based on high latency"""
    mock_prometheus.custom_query.side_effect = [
        [],  # queue_depth
        [{'value': ['1234567890', '0.750']}],  # 750ms latency
        [],  # error_rate
        []   # connections
    ]
    
    collector = CustomMetricsCollector('http://localhost:9090')
    result = collector.evaluate_scaling_need({
        'latency_p95_max_ms': 500
    })
    
    assert result['scale_needed'] is True
    assert any('latency' in r for r in result['reasons'])
    assert result['metrics']['latency_p95_ms'] == 750.0


def test_evaluate_scaling_need_error_rate(mock_prometheus):
    """Test scaling evaluation based on high error rate"""
    mock_prometheus.custom_query.side_effect = [
        [],  # queue_depth
        [],  # latency
        [{'value': ['1234567890', '8.5']}],  # 8.5% error rate
        []   # connections
    ]
    
    collector = CustomMetricsCollector('http://localhost:9090')
    result = collector.evaluate_scaling_need({
        'error_rate_max_percent': 5.0
    })
    
    assert result['scale_needed'] is True
    assert any('Error rate' in r for r in result['reasons'])
    assert result['metrics']['error_rate_percent'] == 8.5


def test_evaluate_scaling_need_connections(mock_prometheus):
    """Test scaling evaluation based on connection count"""
    mock_prometheus.custom_query.side_effect = [
        [],  # queue_depth
        [],  # latency
        [],  # error_rate
        [{'value': ['1234567890', '2500']}]  # 2500 connections
    ]
    
    collector = CustomMetricsCollector('http://localhost:9090')
    result = collector.evaluate_scaling_need({
        'connections_per_node': 1000
    })
    
    assert result['scale_needed'] is True
    assert any('connections' in r for r in result['reasons'])
    assert result['metrics']['active_connections'] == 2500


def test_evaluate_scaling_need_no_scale(mock_prometheus):
    """Test no scaling needed when all metrics normal"""
    mock_prometheus.custom_query.side_effect = [
        [{'value': ['1234567890', '50']}],  # low queue depth
        [{'value': ['1234567890', '0.100']}],  # 100ms latency
        [],  # no errors
        [{'value': ['1234567890', '500']}]  # normal connections
    ]
    
    collector = CustomMetricsCollector('http://localhost:9090')
    result = collector.evaluate_scaling_need({
        'queue_depth_max': 100,
        'latency_p95_max_ms': 500,
        'error_rate_max_percent': 5.0,
        'connections_per_node': 1000
    })
    
    assert result['scale_needed'] is False
    assert len(result['reasons']) == 0


def test_evaluate_scaling_need_multiple_triggers(mock_prometheus):
    """Test scaling with multiple metrics exceeding thresholds"""
    mock_prometheus.custom_query.side_effect = [
        [{'value': ['1234567890', '120']}],  # high queue
        [{'value': ['1234567890', '0.600']}],  # high latency
        [{'value': ['1234567890', '6.5']}],  # high errors
        []
    ]
    
    collector = CustomMetricsCollector('http://localhost:9090')
    result = collector.evaluate_scaling_need({
        'queue_depth_max': 100,
        'latency_p95_max_ms': 500,
        'error_rate_max_percent': 5.0
    })
    
    assert result['scale_needed'] is True
    assert len(result['reasons']) >= 2  # Multiple reasons


def test_get_custom_metrics_convenience_function(mock_prometheus):
    """Test convenience function for getting all custom metrics"""
    mock_prometheus.custom_query.side_effect = [
        [{'value': ['1234567890', '50']}],
        [{'value': ['1234567890', '0.200']}],
        [],
        [{'value': ['1234567890', '800']}]
    ]
    
    result = get_custom_metrics('http://localhost:9090')
    
    assert 'scale_needed' in result
    assert 'metrics' in result
    assert 'reasons' in result


def test_prometheus_error_handling(mock_prometheus):
    """Test graceful error handling for Prometheus failures"""
    mock_prometheus.custom_query.side_effect = Exception("Prometheus unavailable")
    
    collector = CustomMetricsCollector('http://localhost:9090')
    depth = collector.get_queue_depth()
    
    assert depth is None  # Should return None on error, not crash
