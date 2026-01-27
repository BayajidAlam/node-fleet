"""
Scaling decision engine
Evaluates metrics and determines scaling actions
"""

import time
import logging
from typing import Dict, List, Optional

logger = logging.getLogger()

# Scaling thresholds
CPU_SCALE_UP_THRESHOLD = 70.0
CPU_SCALE_DOWN_THRESHOLD = 30.0
MEMORY_SCALE_UP_THRESHOLD = 75.0
MEMORY_SCALE_DOWN_THRESHOLD = 50.0

# Cooldown periods (seconds)
SCALE_UP_COOLDOWN = 300  # 5 minutes
SCALE_DOWN_COOLDOWN = 600  # 10 minutes


class ScalingDecision:
    """Evaluates metrics and decides scaling actions
    
    Note: All node counts refer to WORKER nodes only (master excluded).
    Cluster minimum: 1 master + 2 workers = 3 total nodes
    """
    
    def __init__(self, min_nodes: int, max_nodes: int, current_nodes: int, last_scale_time: int):
        self.min_nodes = min_nodes  # Minimum WORKER nodes (default: 2)
        self.max_nodes = max_nodes  # Maximum WORKER nodes (default: 10)
        self.current_nodes = current_nodes  # Current WORKER nodes (excludes master)
        self.last_scale_time = last_scale_time
    
    def evaluate(self, metrics: Dict[str, float], history: List[Dict] = None, custom_metrics: Dict = None) -> Dict:
        """
        Evaluate metrics and return scaling decision
        
        Args:
            metrics: Current cluster metrics
            history: Historical metrics from state manager
            custom_metrics: Optional custom application metrics
        
        Returns:
            Dictionary with 'action', 'nodes', 'reason'
        """
        cpu = metrics.get("cpu_usage", 0)
        memory = metrics.get("memory_usage", 0)
        pending_pods = metrics.get("pending_pods", 0)
        
        current_time = int(time.time())
        time_since_last_scale = current_time - self.last_scale_time
        
        # 0. Enforce Minimum Node Count
        if self.current_nodes < self.min_nodes:
            nodes_needed = self.min_nodes - self.current_nodes
            return {
                "action": "scale_up",
                "nodes": nodes_needed,
                "reason": f"Enforcing minimum node count ({self.min_nodes})"
            }
        
        # 0.5 Enforce Maximum Node Count
        if self.current_nodes >= self.max_nodes:
            logger.info(f"At or above max capacity ({self.current_nodes}/{self.max_nodes}). Skipping scale-up check.")
            # Still evaluate scale-down below
        else:
            # 1. Evaluate SCALE UP conditions (3-minute sustained load)
            scale_up_reasons = []
        
        # Check if CPU/Mem/Pending exceeds threshold for at least 2 consecutive readings (covering ~3-4 mins)
        sustained_cpu = self._is_sustained_above(cpu, history, 'cpu_usage', CPU_SCALE_UP_THRESHOLD, window=3)
        sustained_memory = self._is_sustained_above(memory, history, 'memory_usage', MEMORY_SCALE_UP_THRESHOLD, window=3)
        sustained_pending = self._is_sustained_above(pending_pods, history, 'pending_pods', 0, window=3)

        if sustained_cpu:
            scale_up_reasons.append(f"CPU sustained > {CPU_SCALE_UP_THRESHOLD}%")
        
        if sustained_memory:
            scale_up_reasons.append(f"Memory sustained > {MEMORY_SCALE_UP_THRESHOLD}%")
        
        if sustained_pending:
            scale_up_reasons.append(f"Pending pods sustained > 0")
        
        # Custom metrics (usually instantaneous is fine for custom triggers)
        if custom_metrics and custom_metrics.get('scale_needed'):
            custom_reasons = custom_metrics.get('reasons', [])
            scale_up_reasons.extend([f"Custom: {r}" for r in custom_reasons])
        
        if scale_up_reasons:
            if time_since_last_scale < SCALE_UP_COOLDOWN:
                logger.info(f"Scale-up needed but in cooldown period ({time_since_last_scale}s)")
                return {"action": "none", "nodes": 0, "reason": "In cooldown"}
            
            if self.current_nodes >= self.max_nodes:
                return {"action": "none", "nodes": 0, "reason": f"At max capacity ({self.max_nodes})"}
            
            # Smart increment
            nodes_to_add = 2 if cpu > 85 or pending_pods > 5 else 1
            nodes_to_add = min(nodes_to_add, self.max_nodes - self.current_nodes)
            
            return {
                "action": "scale_up",
                "nodes": nodes_to_add,
                "reason": ", ".join(scale_up_reasons)
            }
        
        # 2. Evaluate SCALE DOWN conditions (10-minute sustained low utilization)
        # 10 mins = ~5 consecutive readings at 2-min intervals
        sustained_low_util = self._is_sustained_below_all(
            metrics={'cpu_usage': cpu, 'memory_usage': memory, 'pending_pods': pending_pods},
            history=history,
            thresholds={
                'cpu_usage': CPU_SCALE_DOWN_THRESHOLD,
                'memory_usage': MEMORY_SCALE_DOWN_THRESHOLD,
                'pending_pods': 1 # Must be < 1
            },
            window=10
        )
        
        if sustained_low_util:
            if time_since_last_scale < SCALE_DOWN_COOLDOWN:
                logger.info(f"Scale-down possible but in cooldown ({time_since_last_scale}s)")
                return {"action": "none", "nodes": 0, "reason": "In cooldown"}
            
            if self.current_nodes <= self.min_nodes:
                return {"action": "none", "nodes": 0, "reason": "At min capacity"}
            
            return {
                "action": "scale_down",
                "nodes": 1,
                "reason": "Sustained low utilization (10m)"
            }
        
        return {"action": "none", "nodes": 0, "reason": "Stable"}

    def _is_sustained_above(self, current_val, history, key, threshold, window=2):
        """Check if value has been above threshold for N consecutive readings"""
        if current_val <= threshold:
            return False
            
        if not history or len(history) < window - 1:
            return False
            
        # Check last N-1 items in history
        recent_history = history[-(window-1):]
        for item in recent_history:
            if float(item.get(key, 0)) <= threshold:
                return False
        return True

    def _is_sustained_below_all(self, metrics, history, thresholds, window=5):
        """Check if all metrics have been below thresholds for N consecutive readings"""
        # Check current
        for key, threshold in thresholds.items():
            if float(metrics.get(key, 0)) >= threshold:
                return False
                
        if not history or len(history) < window - 1:
            return False
            
        # Check history
        recent_history = history[-(window-1):]
        for item in recent_history:
            for key, threshold in thresholds.items():
                if float(item.get(key, 0)) >= threshold:
                    return False
        return True
