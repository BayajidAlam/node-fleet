"""
Slack notifier
Sends notifications via SNS topic
"""

import os
import logging
import boto3

logger = logging.getLogger()

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")


def send_notification(message: str):
    """
    Send notification to Slack via SNS
    
    Args:
        message: Message text to send
    """
    if not SNS_TOPIC_ARN:
        logger.warning("SNS_TOPIC_ARN not configured, skipping notification")
        return
    
    try:
        sns_client = boto3.client('sns')
        
        response = sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=message,
            Subject='K3s Autoscaler Notification'
        )
        
        logger.info(f"Notification sent: {response['MessageId']}")
        
    except Exception as e:
        logger.error(f"Failed to send notification: {str(e)}")
        # Don't raise - notifications are non-critical
