"""
Unit tests for metrics_collector module
"""

import pytest
import sys
import os
import json
from unittest.mock import Mock, patch, MagicMock

# Add parent directory and lambda directory to path to import lambda modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../lambda')))

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("metrics_collector", os.path.join(os.path.dirname(__file__), '../../lambda/metrics_collector.py'))
metrics_collector_module = importlib.util.module_from_spec(spec)

# Register in sys.modules so patch works
sys.modules['metrics_collector'] = metrics_collector_module
spec.loader.exec_module(metrics_collector_module)
collect_metrics = metrics_collector_module.collect_metrics


@pytest.fixture
def mock_requests():
    """Mock requests.get for Prometheus queries"""
    with patch('metrics_collector.requests.get') as mock_get:
        yield mock_get


def test_collect_metrics_success(mock_requests):
    """Test successful metrics collection"""
    mock_response = MagicMock()
    # Mock sequence of responses for different queries (CPU, Memory, Pending, NodeCount, others...)
    mock_response.json.side_effect = [
        {'status': 'success', 'data': {'result': [{'value': ['0', '75.5']}]}},  # CPU
        {'status': 'success', 'data': {'result': [{'value': ['0', '65.2']}]}},  # Memory
        {'status': 'success', 'data': {'result': [{'value': ['0', '3']}]}},     # Pending
        {'status': 'success', 'data': {'result': [{'value': ['0', '5']}]}},     # Node Count
        {'status': 'success', 'data': {'result': [{'value': ['0', '10.5']}]}},  # Net Rec
        {'status': 'success', 'data': {'result': [{'value': ['0', '8.2']}]}},   # Net Trans
        {'status': 'success', 'data': {'result': [{'value': ['0', '1.5']}]}},   # Disk Read
        {'status': 'success', 'data': {'result': [{'value': ['0', '2.3']}]}},   # Disk Write
    ]
    mock_requests.return_value = mock_response
    
    # Disable cache to test query logic
    metrics = collect_metrics("http://localhost:9090", use_cache=False)
    
    assert metrics["cpu_usage"] == 75.5
    assert metrics["memory_usage"] == 65.2
    assert metrics["pending_pods"] == 3.0
    assert metrics["node_count"] == 5.0
    assert metrics["network_receive_mbps"] == 10.5


def test_collect_metrics_empty_response(mock_requests):
    """Test handling of empty Prometheus response"""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        'status': 'success',
        'data': {'result': []}
    }
    mock_requests.return_value = mock_response
    
    metrics = collect_metrics("http://localhost:9090", use_cache=False)
    
    assert metrics["cpu_usage"] == 0.0
    assert metrics["memory_usage"] == 0.0
    assert metrics["pending_pods"] == 0.0
    assert metrics["node_count"] == 0.0


def test_collect_metrics_no_url():
    """Test error when Prometheus URL not provided"""
    with pytest.raises(ValueError, match="PROMETHEUS_URL"):
        collect_metrics(None)


def test_collect_metrics_connection_error(mock_requests):
    """Test handling of Prometheus connection errors"""
    # Per-metric errors are caught, and 0.0 is returned for that metric
    mock_requests.side_effect = Exception("Connection refused")
    
    metrics = collect_metrics("http://localhost:9090", use_cache=False)
    
    assert metrics["cpu_usage"] == 0.0
    assert metrics["memory_usage"] == 0.0
