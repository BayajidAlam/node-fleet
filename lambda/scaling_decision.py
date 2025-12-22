"""
Scaling decision engine
Evaluates metrics and determines scaling actions
"""

import time
import logging
from typing import Dict

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
    """Evaluates metrics and decides scaling actions"""
    
    def __init__(self, min_nodes: int, max_nodes: int, current_nodes: int, last_scale_time: int):
        self.min_nodes = min_nodes
        self.max_nodes = max_nodes
        self.current_nodes = current_nodes
        self.last_scale_time = last_scale_time
    
    def evaluate(self, metrics: Dict[str, float], custom_metrics: Dict = None) -> Dict:
        """
        Evaluate metrics and return scaling decision
        
        Args:
            metrics: Standard cluster metrics (CPU, memory, pending pods)
            custom_metrics: Optional custom application metrics
        
        Returns:
            Dictionary with 'action' (scale_up/scale_down/none), 'nodes', 'reason'
        """
        cpu = metrics.get("cpu_usage", 0)
        memory = metrics.get("memory_usage", 0)
        pending_pods = metrics.get("pending_pods", 0)
        
        current_time = int(time.time())
        time_since_last_scale = current_time - self.last_scale_time
        
        # Check for scale UP conditions
        scale_up_reasons = []
        
        if cpu > CPU_SCALE_UP_THRESHOLD:
            scale_up_reasons.append(f"CPU > {CPU_SCALE_UP_THRESHOLD}% ({cpu:.1f}%)")
        
        if memory > MEMORY_SCALE_UP_THRESHOLD:
            scale_up_reasons.append(f"Memory > {MEMORY_SCALE_UP_THRESHOLD}% ({memory:.1f}%)")
        
        if pending_pods > 0:
            scale_up_reasons.append(f"Pending pods: {int(pending_pods)}")
        
        # Check custom metrics if provided
        if custom_metrics and custom_metrics.get('scale_needed'):
            custom_reasons = custom_metrics.get('reasons', [])
            scale_up_reasons.extend([f"Custom: {r}" for r in custom_reasons])
        
        if scale_up_reasons:
            # Check cooldown
            if time_since_last_scale < SCALE_UP_COOLDOWN:
                logger.info(f"Scale-up needed but in cooldown period ({time_since_last_scale}s < {SCALE_UP_COOLDOWN}s)")
                return {"action": "none", "nodes": 0, "reason": "In cooldown"}
            
            # Check max nodes
            if self.current_nodes >= self.max_nodes:
                logger.warning(f"At max capacity ({self.max_nodes} nodes)")
                return {"action": "none", "nodes": 0, "reason": f"At max capacity ({self.max_nodes} nodes)"}
            
            # Determine how many nodes to add (1-2 nodes)
            nodes_to_add = 2 if cpu > 80 or pending_pods > 5 else 1
            nodes_to_add = min(nodes_to_add, self.max_nodes - self.current_nodes)
            
            return {
                "action": "scale_up",
                "nodes": nodes_to_add,
                "reason": ", ".join(scale_up_reasons)
            }
        
        # Check for scale DOWN conditions
        scale_down_possible = (
            cpu < CPU_SCALE_DOWN_THRESHOLD and
            memory < MEMORY_SCALE_DOWN_THRESHOLD and
            pending_pods == 0
        )
        
        if scale_down_possible:
            # Check cooldown (longer for scale-down)
            if time_since_last_scale < SCALE_DOWN_COOLDOWN:
                logger.info(f"Scale-down possible but in cooldown period ({time_since_last_scale}s < {SCALE_DOWN_COOLDOWN}s)")
                return {"action": "none", "nodes": 0, "reason": "In cooldown"}
            
            # Check min nodes
            if self.current_nodes <= self.min_nodes:
                logger.info(f"At min capacity ({self.min_nodes} nodes)")
                return {"action": "none", "nodes": 0, "reason": f"At min capacity ({self.min_nodes} nodes)"}
            
            # Remove 1 node at a time for safety
            return {
                "action": "scale_down",
                "nodes": 1,
                "reason": f"Low utilization (CPU: {cpu:.1f}%, Memory: {memory:.1f}%)"
            }
        
        # No scaling needed
        logger.info("Cluster metrics within acceptable range")
        return {
            "action": "none",
            "nodes": 0,
            "reason": f"Stable (CPU: {cpu:.1f}%, Memory: {memory:.1f}%)"
        }
