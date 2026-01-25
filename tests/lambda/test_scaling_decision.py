"""
Unit tests for scaling_decision module
"""

import pytest
import time
import sys
import os

# Import using importlib to avoid 'lambda' reserved keyword
import importlib.util
spec = importlib.util.spec_from_file_location("scaling_decision", os.path.join(os.path.dirname(__file__), '../../lambda/scaling_decision.py'))
scaling_decision_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scaling_decision_module)
ScalingDecision = scaling_decision_module.ScalingDecision


@pytest.fixture
def decision_engine():
    """
    Creates a ScalingDecision instance for testing
    Note: All values refer to WORKER nodes only (master excluded)
    - min_nodes=2: Minimum 2 workers (total cluster = 1 master + 2 workers = 3)
    - max_nodes=10: Maximum 10 workers (total cluster = 11)
    - current_nodes=2: Currently 2 workers running (normal minimum state)
    """
    return ScalingDecision(
        min_nodes=2,  # Minimum WORKER nodes
        max_nodes=10,  # Maximum WORKER nodes
        current_nodes=2,  # Current WORKER nodes (changed from 3 to reflect actual minimum)
        last_scale_time=0
    )


def test_scale_up_high_cpu(decision_engine):
    """Test scale-up decision on high CPU"""
    metrics = {
        "cpu_usage": 75.0,
        "memory_usage": 50.0,
        "pending_pods": 0
    }
    
    # Mock sustained high CPU (2 consecutive readings)
    history = [
        {"cpu_usage": 76.0, "memory_usage": 48.0, "pending_pods": 0},
        {"cpu_usage": 74.0, "memory_usage": 49.0, "pending_pods": 0}
    ]
    
    decision = decision_engine.evaluate(metrics, history=history)
    
    assert decision["action"] == "scale_up"
    assert decision["nodes"] == 1
    assert "CPU" in decision["reason"]


def test_scale_up_pending_pods(decision_engine):
    """Test scale-up decision on pending pods"""
    metrics = {
        "cpu_usage": 50.0,
        "memory_usage": 50.0,
        "pending_pods": 3
    }
    
    # Mock sustained pending pods (2 consecutive readings)
    history = [
        {"cpu_usage": 48.0, "memory_usage": 49.0, "pending_pods": 2},
        {"cpu_usage": 49.0, "memory_usage": 51.0, "pending_pods": 4}
    ]
    
    decision = decision_engine.evaluate(metrics, history=history)
    
    assert decision["action"] == "scale_up"
    assert decision["nodes"] >= 1
    assert "Pending pods" in decision["reason"]


def test_scale_up_high_memory(decision_engine):
    """Test scale-up decision on high memory"""
    metrics = {
        "cpu_usage": 50.0,
        "memory_usage": 80.0,
        "pending_pods": 0
    }
    
    # Mock sustained high memory (2 consecutive readings)
    history = [
        {"cpu_usage": 48.0, "memory_usage": 78.0, "pending_pods": 0},
        {"cpu_usage": 49.0, "memory_usage": 79.0, "pending_pods": 0}
    ]
    
    decision = decision_engine.evaluate(metrics, history=history)
    
    assert decision["action"] == "scale_up"
    assert decision["nodes"] >= 1
    assert "Memory" in decision["reason"]


def test_scale_up_multiple_nodes_extreme_load(decision_engine):
    """Test scale-up adds 2 nodes on extreme load"""
    metrics = {
        "cpu_usage": 85.0,
        "memory_usage": 80.0,
        "pending_pods": 10
    }
    
    # Mock sustained extreme load (2 consecutive readings)
    history = [
        {"cpu_usage": 86.0, "memory_usage": 79.0, "pending_pods": 12},
        {"cpu_usage": 87.0, "memory_usage": 81.0, "pending_pods": 8}
    ]
    
    decision = decision_engine.evaluate(metrics, history=history)
    
    assert decision["action"] == "scale_up"
    assert decision["nodes"] == 2


def test_scale_up_blocked_at_max(decision_engine):
    """Test scale-up blocked when at max capacity"""
    decision_engine.current_nodes = 10
    
    metrics = {
        "cpu_usage": 85.0,
        "memory_usage": 80.0,
        "pending_pods": 5
    }
    
    # Mock sustained high load but at max capacity
    history = [
        {"cpu_usage": 86.0, "memory_usage": 79.0, "pending_pods": 6},
        {"cpu_usage": 87.0, "memory_usage": 81.0, "pending_pods": 4}
    ]
    
    decision = decision_engine.evaluate(metrics, history=history)
    
    assert decision["action"] == "none"
    assert "max capacity" in decision["reason"]


def test_scale_up_cooldown(decision_engine):
    """Test scale-up blocked during cooldown"""
    decision_engine.last_scale_time = int(time.time()) - 100  # 100s ago
    
    metrics = {
        "cpu_usage": 75.0,
        "memory_usage": 50.0,
        "pending_pods": 0
    }
    
    # Mock sustained high CPU but in cooldown
    history = [
        {"cpu_usage": 76.0, "memory_usage": 48.0, "pending_pods": 0},
        {"cpu_usage": 74.0, "memory_usage": 49.0, "pending_pods": 0}
    ]
    
    decision = decision_engine.evaluate(metrics, history=history)
    
    assert decision["action"] == "none"
    assert "cooldown" in decision["reason"]


def test_scale_down_low_utilization(decision_engine):
    """Test scale-down on low utilization"""
    decision_engine.current_nodes = 4  # Start with 4 workers (above minimum of 2)
    decision_engine.last_scale_time = 0  # Long time ago
    
    metrics = {
        "cpu_usage": 25.0,
        "memory_usage": 40.0,
        "pending_pods": 0
    }
    
    # Mock sustained low utilization (5 consecutive readings for scale-down)
    history = [
        {"cpu_usage": 26.0, "memory_usage": 38.0, "pending_pods": 0},
        {"cpu_usage": 28.0, "memory_usage": 42.0, "pending_pods": 0},
        {"cpu_usage": 24.0, "memory_usage": 39.0, "pending_pods": 0},
        {"cpu_usage": 27.0, "memory_usage": 41.0, "pending_pods": 0},
        {"cpu_usage": 25.0, "memory_usage": 40.0, "pending_pods": 0}
    ]
    
    decision = decision_engine.evaluate(metrics, history=history)
    
    assert decision["action"] == "scale_down"
    assert decision["nodes"] == 1
    assert "Sustained low utilization" in decision["reason"]


def test_scale_down_blocked_at_min(decision_engine):
    """Test scale-down blocked when at min capacity"""
    decision_engine.current_nodes = 2
    decision_engine.last_scale_time = 0
    
    metrics = {
        "cpu_usage": 20.0,
        "memory_usage": 30.0,
        "pending_pods": 0
    }
    
    # Mock sustained low utilization but at min capacity
    history = [
        {"cpu_usage": 22.0, "memory_usage": 28.0, "pending_pods": 0},
        {"cpu_usage": 21.0, "memory_usage": 32.0, "pending_pods": 0},
        {"cpu_usage": 19.0, "memory_usage": 29.0, "pending_pods": 0},
        {"cpu_usage": 23.0, "memory_usage": 31.0, "pending_pods": 0},
        {"cpu_usage": 20.0, "memory_usage": 30.0, "pending_pods": 0}
    ]
    
    decision = decision_engine.evaluate(metrics, history=history)
    
    assert decision["action"] == "none"
    assert "min capacity" in decision["reason"]


def test_scale_down_blocked_by_pending_pods(decision_engine):
    """Test scale-down blocked when pending pods exist"""
    decision_engine.last_scale_time = 0
    
    metrics = {
        "cpu_usage": 25.0,
        "memory_usage": 40.0,
        "pending_pods": 2
    }
    
    decision = decision_engine.evaluate(metrics)
    
    assert decision["action"] == "none"


def test_no_scaling_stable_metrics(decision_engine):
    """Test no scaling when metrics are stable"""
    metrics = {
        "cpu_usage": 50.0,
        "memory_usage": 55.0,
        "pending_pods": 0
    }
    
    decision = decision_engine.evaluate(metrics)
    
    assert decision["action"] == "none"
    assert decision["nodes"] == 0
