"""
Main Lambda handler for K3s autoscaler
Orchestrates the 5-step scaling workflow with predictive scaling:
1. Query Prometheus metrics
2. Acquire DynamoDB lock
3. Decide scaling action (reactive + predictive)
4. Execute EC2 scaling
5. Send Slack notification
"""

import os
import logging
from datetime import datetime
from typing import Dict, Any
from metrics_collector import collect_metrics
from state_manager import StateManager
from ec2_manager import EC2Manager
from slack_notifier import send_notification
from scaling_decision import ScalingDecision
from predictive_scaling import PredictiveScaler
from custom_metrics import get_custom_metrics

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
CLUSTER_ID = os.environ.get("CLUSTER_ID", "node-fleet-cluster")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL")
STATE_TABLE = os.environ.get("STATE_TABLE")
METRICS_HISTORY_TABLE = os.environ.get("METRICS_HISTORY_TABLE")
MIN_NODES = int(os.environ.get("MIN_NODES", "2"))
MAX_NODES = int(os.environ.get("MAX_NODES", "10"))
WORKER_LAUNCH_TEMPLATE_ID = os.environ.get("WORKER_LAUNCH_TEMPLATE_ID")
WORKER_SPOT_TEMPLATE_ID = os.environ.get("WORKER_SPOT_TEMPLATE_ID")
SPOT_PERCENTAGE = int(os.environ.get("SPOT_PERCENTAGE", "70"))
ENABLE_PREDICTIVE_SCALING = os.environ.get("ENABLE_PREDICTIVE_SCALING", "true").lower() == "true"
ENABLE_CUSTOM_METRICS = os.environ.get("ENABLE_CUSTOM_METRICS", "false").lower() == "true"

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler invoked by EventBridge every 2 minutes
    """
    logger.info(f"Autoscaler triggered for cluster: {CLUSTER_ID}")
    
    try:
        # Step 1: Collect Prometheus metrics
        logger.info("Step 1: Collecting metrics from Prometheus")
        metrics = collect_metrics(PROMETHEUS_URL)
        logger.info(f"Metrics collected: {metrics}")
        
        # Step 2: Acquire DynamoDB lock
        logger.info("Step 2: Acquiring DynamoDB lock")
        state_manager = StateManager(STATE_TABLE, CLUSTER_ID)
        
        if not state_manager.acquire_lock():
            logger.warning("Could not acquire lock - another scaling operation in progress")
            return {
                "statusCode": 200,
                "body": "Skipped: Scaling already in progress"
            }
        
        try:
            # Get current cluster state
            current_state = state_manager.get_state()
            current_nodes = current_state.get("node_count", MIN_NODES)
            
            # Store current metrics for predictive analysis
            if ENABLE_PREDICTIVE_SCALING and METRICS_HISTORY_TABLE:
                logger.info("Storing metrics for predictive analysis")
                predictor = PredictiveScaler(METRICS_HISTORY_TABLE)
                predictor.store_metrics(
                    timestamp=datetime.utcnow(),
                    cpu_percent=metrics.get('cpu_percent', 0),
                    memory_percent=metrics.get('memory_percent', 0),
                    pending_pods=metrics.get('pending_pods', 0),
                    node_count=current_nodes
                )
            
            # Step 3: Decide scaling action (reactive + predictive + custom metrics)
            logger.info(f"Step 3: Evaluating scaling decision (current nodes: {current_nodes})")
            decision_engine = ScalingDecision(
                min_nodes=MIN_NODES,
                max_nodes=MAX_NODES,
                current_nodes=current_nodes,
                last_scale_time=current_state.get("last_scale_time", 0)
            )
            
            # Collect custom application metrics if enabled
            custom_metrics_eval = None
            if ENABLE_CUSTOM_METRICS and PROMETHEUS_URL:
                logger.info("Collecting custom application metrics")
                try:
                    custom_metrics_eval = get_custom_metrics(PROMETHEUS_URL)
                    logger.info(f"Custom metrics: {custom_metrics_eval}")
                except Exception as e:
                    logger.warning(f"Failed to collect custom metrics: {e}")
            
            # Reactive scaling decision (includes custom metrics)
            action = decision_engine.evaluate(metrics, custom_metrics=custom_metrics_eval)
            logger.info(f"Reactive scaling decision: {action}")
            
            # Predictive scaling check (if enabled and no immediate action needed)
            if ENABLE_PREDICTIVE_SCALING and METRICS_HISTORY_TABLE and action["action"] == "none":
                logger.info("Checking predictive scaling recommendations")
                predictor = PredictiveScaler(METRICS_HISTORY_TABLE)
                prediction = predictor.predict_next_hour_load(metrics)
                
                if prediction:
                    should_scale, reason = predictor.should_proactive_scale_up(
                        current_metrics=metrics,
                        prediction=prediction
                    )
                    
                    if should_scale:
                        recommended_nodes = predictor.calculate_recommended_nodes(
                            prediction=prediction,
                            current_nodes=current_nodes
                        )
                        nodes_to_add = min(recommended_nodes - current_nodes, 2)  # Max 2 nodes proactively
                        
                        if nodes_to_add > 0:
                            action = {
                                "action": "scale_up",
                                "nodes": nodes_to_add,
                                "reason": f"Predictive: {reason}"
                            }
                            logger.info(f"Predictive scaling decision: {action}")
            
            if action["action"] == "none":
                logger.info("No scaling action needed")
                return {
                    "statusCode": 200,
                    "body": f"No scaling needed. Metrics: {metrics}"
                }
            
            # Step 4: Execute EC2 scaling
            logger.info(f"Step 4: Executing scaling action: {action['action']}")
            ec2_manager = EC2Manager(
                worker_template_id=WORKER_LAUNCH_TEMPLATE_ID,
                worker_spot_template_id=WORKER_SPOT_TEMPLATE_ID,
                spot_percentage=SPOT_PERCENTAGE
            )
            
            if action["action"] == "scale_up":
                result = ec2_manager.scale_up(
                    nodes_to_add=action["nodes"],
                    reason=action["reason"]
                )
            else:  # scale_down
                result = ec2_manager.scale_down(
                    nodes_to_remove=action["nodes"],
                    reason=action["reason"]
                )
            
            # Update state in DynamoDB
            new_node_count = current_nodes + (action["nodes"] if action["action"] == "scale_up" else -action["nodes"])
            state_manager.update_state(new_node_count)
            
            # Step 5: Send Slack notification
            logger.info("Step 5: Sending Slack notification")
            notification_message = format_notification(action, result, new_node_count, metrics)
            send_notification(notification_message)
            
            return {
                "statusCode": 200,
                "body": f"Scaling completed: {action['action']} - {result}"
            }
            
        finally:
            # Always release lock
            state_manager.release_lock()
            logger.info("Lock released")
    
    except Exception as e:
        logger.error(f"Autoscaler error: {str(e)}", exc_info=True)
        
        # Send error notification
        error_message = f"🔴 *Autoscaler Error*\n```{str(e)}```"
        try:
            send_notification(error_message)
        except:
            pass  # Don't fail if notification fails
        
        raise


def format_notification(action: Dict, result: Dict, new_node_count: int, metrics: Dict) -> str:
    """Format Slack notification message"""
    emoji = "🟢" if action["action"] == "scale_up" else "🔵"
    action_text = "Scale Up" if action["action"] == "scale_up" else "Scale Down"
    
    message = f"{emoji} *{action_text}*\n"
    message += f"*Reason:* {action['reason']}\n"
    message += f"*Nodes Changed:* {action['nodes']}\n"
    message += f"*Total Nodes:* {new_node_count}\n"
    message += f"*Metrics:*\n"
    message += f"  • CPU: {metrics.get('cpu_usage', 0):.1f}%\n"
    message += f"  • Memory: {metrics.get('memory_usage', 0):.1f}%\n"
    message += f"  • Pending Pods: {metrics.get('pending_pods', 0)}\n"
    
    if result.get("instance_ids"):
        message += f"*Instance IDs:* {', '.join(result['instance_ids'][:3])}"
    
    return message
