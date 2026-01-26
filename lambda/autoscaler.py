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
import boto3
from datetime import datetime, timezone
from typing import Dict, Any
from metrics_collector import collect_metrics
from state_manager import StateManager
from ec2_manager import EC2Manager
from slack_notifier import send_notification
from scaling_decision import ScalingDecision
from predictive_scaling import PredictiveScaler
from custom_metrics import get_custom_metrics
from cost_optimizer import get_cost_recommendations

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# CloudWatch client for custom metrics
cloudwatch = boto3.client('cloudwatch')

# Environment variables
CLUSTER_ID = os.environ.get("CLUSTER_ID", "node-fleet-cluster")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL")
STATE_TABLE = os.environ.get("STATE_TABLE")
METRICS_HISTORY_TABLE = os.environ.get("METRICS_HISTORY_TABLE")
MIN_NODES = int(os.environ.get("MIN_NODES", "2"))  # Minimum WORKER nodes (excludes master)
MAX_NODES = int(os.environ.get("MAX_NODES", "10"))  # Maximum WORKER nodes (excludes master)
WORKER_LAUNCH_TEMPLATE_ID = os.environ.get("WORKER_LAUNCH_TEMPLATE_ID")
WORKER_SPOT_TEMPLATE_ID = os.environ.get("WORKER_SPOT_TEMPLATE_ID")
SPOT_PERCENTAGE = int(os.environ.get("SPOT_PERCENTAGE", "70"))
ENABLE_PREDICTIVE_SCALING = os.environ.get("ENABLE_PREDICTIVE_SCALING", "true").lower() == "true"
ENABLE_CUSTOM_METRICS = os.environ.get("ENABLE_CUSTOM_METRICS", "false").lower() == "true"

def get_prometheus_credentials():
    """Get Prometheus credentials from Secrets Manager or Env Vars"""
    username = os.environ.get("PROMETHEUS_USERNAME", "admin")
    password = os.environ.get("PROMETHEUS_PASSWORD", "prompassword")
    
    # If using defaults, try fetching from Secrets Manager
    if password == "prompassword":
        try:
            import json
            client = boto3.client('secretsmanager')
            secret_name = "node-fleet/prometheus-auth"
            response = client.get_secret_value(SecretId=secret_name)
            if 'SecretString' in response:
                creds = json.loads(response['SecretString'])
                username = creds.get('username', username)
                password = creds.get('password', password)
                logger.info("Loaded Prometheus credentials from Secrets Manager")
        except Exception as e:
            logger.warning(f"Failed to load Prometheus secrets: {e}")
            
    return username, password

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler invoked by EventBridge every 2 minutes
    """
    logger.info(f"Autoscaler triggered for cluster: {CLUSTER_ID}")
    
    # Handle Spot Instance Interruption
    if event.get("detail-type") == "EC2 Spot Instance Interruption Warning":
        logger.warning(f"RECEIVED SPOT INTERRUPT WARNING: {event}")
        try:
            instance_id = event.get("detail", {}).get("instance-id")
            if not instance_id:
                logger.error("Spot event missing instance-id")
                return {"statusCode": 400, "body": "Missing instance-id"}
                
            logger.info(f"Handling interruption for {instance_id}")
            
            ec2_manager = EC2Manager(
                worker_template_id=WORKER_LAUNCH_TEMPLATE_ID,
                worker_spot_template_id=WORKER_SPOT_TEMPLATE_ID,
                spot_percentage=SPOT_PERCENTAGE
            )
            
            result = ec2_manager.handle_spot_interruption_event(instance_id)
            
            # Notify Slack
            emoji = "⚠️" if result.get("success") else "❌"
            msg = f"{emoji} **Spot Interruption Warning**\n"
            msg += f"*Instance:* {instance_id}\n"
            msg += f"*Action:* {result.get('action', 'failed')}\n"
            if result.get("node"):
                msg += f"*Node:* {result['node']}\n"
            if result.get("reason"):
                msg += f"*Reason:* {result['reason']}"
                
            send_notification(msg)
            
            return {
                "statusCode": 200, 
                "body": f"Spot handled: {result}"
            }
            
        except Exception as e:
            logger.error(f"Error handling spot event: {e}")
            send_notification(f"❌ Failed to handle spot interruption for {event}: {e}")
            raise e

    try:
        # Step 1: Collect Prometheus metrics
        logger.info("Step 1: Collecting metrics from Prometheus")
        
        # Get credentials (handles dynamic secret fetching)
        prom_user, prom_pass = get_prometheus_credentials()
        
        metrics = collect_metrics(PROMETHEUS_URL, prom_user, prom_pass)
        

        
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
            # Get current cluster state (includes metrics_history)
            current_state = state_manager.get_state()
            metric_node_count = int(metrics.get("node_count", 0))
            stored_node_count = current_state.get("node_count", MIN_NODES)
            
            # Use metrics as primary source (reconcile if state is drifting or initializing)
            if metric_node_count > 0:
                current_nodes = metric_node_count
                if current_nodes != stored_node_count:
                    logger.info(f"Reconciling state: Metrics show {current_nodes} nodes, State showed {stored_node_count}. Using Metrics.")
            else:
                 # If metrics show 0, it might be an error or true zero. 
                 # If it's true zero, we want to start scaling. 
                 # Trust metrics if it's 0 but not None? 
                 # metrics.get defaults to 0.0. 
                 # We'll use 0 if Metrics collected successfully (we know this from logic above)
                 # But we must be careful of failed scraping returning 0.
                 # Let's trust it for now to solve the bootstrap issue.
                 current_nodes = metric_node_count
                 logger.info(f"Metrics show 0 nodes. Using 0 to trigger bootstrap scaling.")

            history = current_state.get("metrics_history", [])
            
            # Step 2.5: Update metrics history with current reading
            logger.info("Updating metrics history in state")
            state_manager.update_metrics_history(metrics)

            # Store current metrics for predictive analysis (separate from reactive history)
            if ENABLE_PREDICTIVE_SCALING and METRICS_HISTORY_TABLE:
                logger.info("Storing metrics for predictive analysis")
                predictor = PredictiveScaler(METRICS_HISTORY_TABLE)
                predictor.store_metrics(
                    timestamp=datetime.now(timezone.utc),
                    cpu_percent=metrics.get('cpu_usage', 0),
                    memory_percent=metrics.get('memory_usage', 0),
                    pending_pods=metrics.get('pending_pods', 0),
                    node_count=current_nodes
                )
            
            # Step 3: Decide scaling action (reactive + predictive + custom metrics)
            logger.info(f"Step 3: Evaluating scaling decision (current nodes: {current_nodes})")
            
            decision_engine = ScalingDecision(
                min_nodes=MIN_NODES,
                max_nodes=MAX_NODES,
                current_nodes=current_nodes,
                last_scale_time=current_state.get('last_scale_time', 0)
            )
            
            # Collect custom application metrics if enabled
            custom_metrics_eval = None
            if ENABLE_CUSTOM_METRICS and PROMETHEUS_URL:
                logger.info("Collecting custom application metrics")
                try:
                    custom_metrics_eval = get_custom_metrics(
                        PROMETHEUS_URL, 
                        PROMETHEUS_USERNAME, 
                        PROMETHEUS_PASSWORD
                    )
                    logger.info(f"Custom metrics: {custom_metrics_eval}")
                except Exception as e:
                    logger.warning(f"Failed to collect custom metrics: {e}")
            
            # Reactive scaling decision (includes history for sustained load check)
            action = decision_engine.evaluate(metrics, history=history, custom_metrics=custom_metrics_eval)
            logger.info(f"Reactive scaling decision: {action}")
            
            # Predictive scaling check (only if enabled, no immediate action needed, and close to next hour)
            # "Pre-scale 10 minutes before known high-traffic periods" implies checking ~10 mins before hour change
            current_minute = datetime.now(timezone.utc).minute
            if ENABLE_PREDICTIVE_SCALING and METRICS_HISTORY_TABLE and action["action"] == "none" and current_minute >= 50:
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
                # Still publish metrics for monitoring
                publish_cloudwatch_metrics(
                    action='none',
                    metrics=metrics,
                    new_node_count=current_nodes,
                    success=True
                )
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
            
            # Publish CloudWatch metrics
            node_join_latency = result.get('node_join_latency_ms')
            publish_cloudwatch_metrics(
                action=action['action'],
                metrics=metrics,
                new_node_count=new_node_count,
                success=True,
                node_join_latency_ms=node_join_latency
            )
            
            # Step 5: Send Slack notification
            logger.info("Step 5: Sending Slack notification")
            notification_message = format_notification(action, result, new_node_count, metrics)
            send_notification(notification_message)
            
            # BONUS: Generate cost optimization recommendations (weekly)
            if should_generate_cost_report():
                logger.info("Generating weekly cost optimization report")
                try:
                    cost_recommendations = get_cost_recommendations(
                        cluster_id=CLUSTER_ID,
                        current_metrics=metrics,
                        current_nodes=new_node_count
                    )
                    
                    if cost_recommendations.get('recommendations'):
                        send_cost_recommendations_notification(cost_recommendations)
                        logger.info(f"Cost recommendations sent: {len(cost_recommendations['recommendations'])} suggestions")
                except Exception as e:
                    logger.error(f"Failed to generate cost recommendations: {e}")
            
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
        
        # Publish failure metrics
        try:
            error_type = type(e).__name__
            publish_cloudwatch_metrics(
                action='error',
                metrics=metrics if 'metrics' in locals() else {},
                new_node_count=current_nodes if 'current_nodes' in locals() else 0,
                success=False,
                error_type=error_type
            )
        except:
            pass  # Don't fail if metrics publishing fails
        
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


def publish_cloudwatch_metrics(action: str, metrics: Dict, new_node_count: int, success: bool = True, error_type: str = None, node_join_latency_ms: int = None) -> None:
    """Publish custom CloudWatch metrics for monitoring and alarms"""
    try:
        metric_data = [
            # Autoscaler invocation count
            {
                'MetricName': 'AutoscalerInvocations',
                'Value': 1,
                'Unit': 'Count',
                'Timestamp': datetime.now(timezone.utc)
            },
            # Current node count
            {
                'MetricName': 'CurrentNodeCount',
                'Value': new_node_count,
                'Unit': 'Count',
                'Timestamp': datetime.now(timezone.utc)
            },
            # Cluster CPU utilization
            {
                'MetricName': 'ClusterCPUUtilization',
                'Value': metrics.get('cpu_usage', 0),
                'Unit': 'Percent',
                'Timestamp': datetime.now(timezone.utc)
            },
            # Cluster memory utilization
            {
                'MetricName': 'ClusterMemoryUtilization',
                'Value': metrics.get('memory_usage', 0),
                'Unit': 'Percent',
                'Timestamp': datetime.now(timezone.utc)
            },
            # Pending pods count
            {
                'MetricName': 'PendingPods',
                'Value': metrics.get('pending_pods', 0),
                'Unit': 'Count',
                'Timestamp': datetime.now(timezone.utc)
            }
        ]
        
        # Add scaling event metrics
        if action == 'scale_up':
            metric_data.append({
                'MetricName': 'ScaleUpEvents',
                'Value': 1,
                'Unit': 'Count',
                'Timestamp': datetime.now(timezone.utc)
            })
        elif action == 'scale_down':
            metric_data.append({
                'MetricName': 'ScaleDownEvents',
                'Value': 1,
                'Unit': 'Count',
                'Timestamp': datetime.now(timezone.utc)
            })
        
        # Add failure metrics if not successful
        if not success:
            metric_data.append({
                'MetricName': 'ScalingFailures',
                'Value': 1,
                'Unit': 'Count',
                'Timestamp': datetime.now(timezone.utc),
                'Dimensions': [
                    {
                        'Name': 'ErrorType',
                        'Value': error_type or 'Unknown'
                    }
                ]
            })
        
        # Add node join latency if provided
        if node_join_latency_ms is not None:
            metric_data.append({
                'MetricName': 'NodeJoinLatency',
                'Value': node_join_latency_ms,
                'Unit': 'Milliseconds',
                'Timestamp': datetime.now(timezone.utc)
            })
        
        # Publish metrics in batches (CloudWatch allows max 20 per call)
        for i in range(0, len(metric_data), 20):
            batch = metric_data[i:i+20]
            cloudwatch.put_metric_data(
                Namespace='node-fleet',
                MetricData=batch
            )
        
        logger.info(f"Published {len(metric_data)} CloudWatch metrics")
    
    except Exception as e:
        logger.warning(f"Failed to publish CloudWatch metrics: {e}")


def should_generate_cost_report() -> bool:
    """
    Determine if cost optimization report should be generated
    Run once per week on Sundays at noon
    """
    now = datetime.now(timezone.utc)
    
    # Only on Sundays (weekday 6) between 12:00-12:10 UTC
    return now.weekday() == 6 and now.hour == 12 and now.minute < 10


def send_cost_recommendations_notification(recommendations: Dict) -> None:
    """
    Send cost optimization recommendations to Slack
    
    Args:
        recommendations: Cost recommendations from cost_optimizer
    """
    total_savings = recommendations.get('potential_savings_percent', 0)
    rec_list = recommendations.get('recommendations', [])
    
    if not rec_list:
        return
    
    # Build Slack message
    message = f"💰 **Weekly Cost Optimization Report** ({recommendations['timestamp'][:10]})\n\n"
    message += f"**Potential Savings: {total_savings:.1f}%**\n\n"
    message += f"Found {len(rec_list)} optimization opportunities:\n\n"
    
    for i, rec in enumerate(rec_list, 1):
        severity_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(rec['severity'], "⚪")
        message += f"{severity_emoji} **{i}. {rec['type'].replace('_', ' ').title()}**\n"
        message += f"   • {rec['message']}\n"
        message += f"   • Action: {rec['action']}\n"
        message += f"   • Savings: {rec['savings_percent']:.1f}%\n"
        message += f"   • Impact: {rec['impact']}\n\n"
    
    message += "_Recommendations are suggestions - evaluate before implementing._"
    
    send_notification(message)
