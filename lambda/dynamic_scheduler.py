"""
Dynamic scheduler for autoscaler Lambda
Adjusts EventBridge trigger frequency based on cluster activity
"""

import logging
import boto3
import os
from typing import Dict, Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cloudwatch = boto3.client('cloudwatch')
events = boto3.client('events')
dynamodb = boto3.resource('dynamodb')

# Environment variables
RULE_NAME = os.environ.get('RULE_NAME')
STATE_TABLE = os.environ.get('STATE_TABLE')
CLUSTER_ID = os.environ.get('CLUSTER_ID', 'node-fleet-cluster')

# Scheduling thresholds (pinned to 1 min for precise scaling timing)
HIGH_ACTIVITY_INTERVAL = 1
NORMAL_INTERVAL = 1
LOW_ACTIVITY_INTERVAL = 1


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Adjust autoscaler Lambda frequency based on cluster activity
    Triggered every 10 minutes to evaluate scheduling needs
    """
    logger.info(f"Evaluating autoscaler scheduling for cluster: {CLUSTER_ID}")
    
    try:
        # Get recent scaling activity
        activity_level = assess_cluster_activity()
        logger.info(f"Cluster activity level: {activity_level}")
        
        # Determine optimal interval
        if activity_level == "high":
            new_interval = HIGH_ACTIVITY_INTERVAL
            reason = "High scaling activity detected"
        elif activity_level == "low":
            new_interval = LOW_ACTIVITY_INTERVAL
            reason = "Low scaling activity, reducing frequency"
        else:
            new_interval = NORMAL_INTERVAL
            reason = "Normal activity level"
        
        # Update EventBridge schedule
        current_interval = get_current_schedule_interval()
        
        if current_interval != new_interval:
            update_schedule(new_interval)
            logger.info(f"Updated autoscaler interval: {current_interval} → {new_interval} minutes. Reason: {reason}")
        else:
            logger.info(f"Autoscaler interval unchanged: {current_interval} minutes")
        
        return {
            "statusCode": 200,
            "body": {
                "activity_level": activity_level,
                "interval_minutes": new_interval,
                "reason": reason
            }
        }
        
    except Exception as e:
        logger.error(f"Error adjusting autoscaler schedule: {e}")
        raise


def assess_cluster_activity() -> str:
    """
    Assess cluster activity level based on recent metrics
    
    Returns:
        "high", "normal", or "low"
    """
    try:
        # Check scaling events in last 30 minutes
        response = cloudwatch.get_metric_statistics(
            Namespace='NodeFleet/Autoscaler',
            MetricName='ScalingEvents',
            Dimensions=[{'Name': 'ClusterID', 'Value': CLUSTER_ID}],
            StartTime=get_time_minutes_ago(30),
            EndTime=get_current_time(),
            Period=1800,  # 30 minutes
            Statistics=['Sum']
        )
        
        scaling_events = 0
        if response['Datapoints']:
            scaling_events = int(response['Datapoints'][0]['Sum'])
        
        # Check CPU volatility (standard deviation)
        cpu_response = cloudwatch.get_metric_statistics(
            Namespace='NodeFleet/Autoscaler',
            MetricName='ClusterCPU',
            Dimensions=[{'Name': 'ClusterID', 'Value': CLUSTER_ID}],
            StartTime=get_time_minutes_ago(30),
            EndTime=get_current_time(),
            Period=300,  # 5 minute intervals
            Statistics=['Average']
        )
        
        cpu_volatility = 0
        if len(cpu_response['Datapoints']) >= 2:
            values = [dp['Average'] for dp in cpu_response['Datapoints']]
            cpu_volatility = calculate_std_dev(values)
        
        # Classify activity level
        if scaling_events >= 3 or cpu_volatility > 20:
            return "high"
        elif scaling_events == 0 and cpu_volatility < 5:
            return "low"
        else:
            return "normal"
            
    except Exception as e:
        logger.error(f"Error assessing activity: {e}")
        return "normal"  # Default to normal on error


def get_current_schedule_interval() -> int:
    """
    Get current EventBridge schedule interval in minutes
    
    Returns:
        Current interval in minutes
    """
    try:
        response = events.describe_rule(Name=RULE_NAME)
        schedule = response['ScheduleExpression']
        
        # Parse "rate(X minutes)" or "rate(X minute)"
        if 'minute' in schedule:
            interval = int(schedule.split('(')[1].split()[0])
            return interval
        
        return 2  # Default
        
    except Exception as e:
        logger.error(f"Error getting current schedule: {e}")
        return 2


def update_schedule(interval_minutes: int) -> None:
    """
    Update EventBridge rule schedule
    
    Args:
        interval_minutes: New interval in minutes
    """
    try:
        schedule_expr = f"rate({interval_minutes} {'minute' if interval_minutes == 1 else 'minutes'})"
        
        events.put_rule(
            Name=RULE_NAME,
            ScheduleExpression=schedule_expr,
            State='ENABLED',
            Description=f"Trigger K3s autoscaler every {interval_minutes} minutes (dynamically adjusted)"
        )
        
        logger.info(f"Updated EventBridge rule {RULE_NAME} to: {schedule_expr}")
        
    except Exception as e:
        logger.error(f"Error updating schedule: {e}")
        raise


def get_time_minutes_ago(minutes: int):
    """Get datetime object for N minutes ago"""
    from datetime import datetime, timedelta
    return datetime.utcnow() - timedelta(minutes=minutes)


def get_current_time():
    """Get current datetime object"""
    from datetime import datetime
    return datetime.utcnow()


def calculate_std_dev(values: list) -> float:
    """Calculate standard deviation of a list"""
    if not values:
        return 0
    
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return variance ** 0.5
