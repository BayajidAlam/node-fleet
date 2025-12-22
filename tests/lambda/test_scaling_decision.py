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
    """Create scaling decision engine with default config"""
    return ScalingDecision(
        min_nodes=2,
        max_nodes=10,
        current_nodes=3,
        last_scale_time=0
    )


def test_scale_up_high_cpu(decision_engine):
    """Test scale-up decision on high CPU"""
    metrics = {
        "cpu_usage": 75.0,
        "memory_usage": 50.0,
        "pending_pods": 0
    }
    
    decision = decision_engine.evaluate(metrics)
    
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
    
    decision = decision_engine.evaluate(metrics)
    
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
    
    decision = decision_engine.evaluate(metrics)
    
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
    
    decision = decision_engine.evaluate(metrics)
    
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
    
    decision = decision_engine.evaluate(metrics)
    
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
    
    decision = decision_engine.evaluate(metrics)
    
    assert decision["action"] == "none"
    assert "cooldown" in decision["reason"]


def test_scale_down_low_utilization(decision_engine):
    """Test scale-down on low utilization"""
    decision_engine.last_scale_time = 0  # Long time ago
    
    metrics = {
        "cpu_usage": 25.0,
        "memory_usage": 40.0,
        "pending_pods": 0
    }
    
    decision = decision_engine.evaluate(metrics)
    
    assert decision["action"] == "scale_down"
    assert decision["nodes"] == 1
    assert "Low utilization" in decision["reason"]


def test_scale_down_blocked_at_min(decision_engine):
    """Test scale-down blocked when at min capacity"""
    decision_engine.current_nodes = 2
    decision_engine.last_scale_time = 0
    
    metrics = {
        "cpu_usage": 20.0,
        "memory_usage": 30.0,
        "pending_pods": 0
    }
    
    decision = decision_engine.evaluate(metrics)
    
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
