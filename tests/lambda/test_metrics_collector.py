"""
Unit tests for metrics_collector module
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("metrics_collector", os.path.join(os.path.dirname(__file__), '../../lambda/metrics_collector.py'))
metrics_collector_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics_collector_module)
collect_metrics = metrics_collector_module.collect_metrics


@pytest.fixture
def mock_prometheus():
    """Mock Prometheus client"""
    with patch('lambda.metrics_collector.PrometheusConnect') as mock:
        prom_instance = MagicMock()
        mock.return_value = prom_instance
        yield prom_instance


def test_collect_metrics_success(mock_prometheus):
    """Test successful metrics collection"""
    # Mock Prometheus responses
    mock_prometheus.custom_query.side_effect = [
        [{"value": [1234567890, "75.5"]}],  # CPU
        [{"value": [1234567890, "65.2"]}],  # Memory
        [{"value": [1234567890, "3"]}],     # Pending pods
        [{"value": [1234567890, "5"]}],     # Node count
    ]
    
    metrics = collect_metrics("http://localhost:9090")
    
    assert metrics["cpu_usage"] == 75.5
    assert metrics["memory_usage"] == 65.2
    assert metrics["pending_pods"] == 3.0
    assert metrics["node_count"] == 5.0


def test_collect_metrics_empty_response(mock_prometheus):
    """Test handling of empty Prometheus response"""
    mock_prometheus.custom_query.return_value = []
    
    metrics = collect_metrics("http://localhost:9090")
    
    assert metrics["cpu_usage"] == 0.0
    assert metrics["memory_usage"] == 0.0
    assert metrics["pending_pods"] == 0.0
    assert metrics["node_count"] == 0.0


def test_collect_metrics_no_url():
    """Test error when Prometheus URL not provided"""
    with pytest.raises(ValueError, match="PROMETHEUS_URL"):
        collect_metrics(None)


def test_collect_metrics_connection_error(mock_prometheus):
    """Test handling of Prometheus connection errors"""
    mock_prometheus.custom_query.side_effect = Exception("Connection refused")
    
    with pytest.raises(Exception):
        collect_metrics("http://localhost:9090")
