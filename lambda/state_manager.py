"""
DynamoDB state manager
Handles cluster state and distributed locking
"""

import time
import logging
import boto3
from botocore.exceptions import ClientError
from typing import Dict, Optional

logger = logging.getLogger()


class StateManager:
    """Manages cluster state in DynamoDB with distributed locking"""
    
    def __init__(self, table_name: str, cluster_id: str):
        self.table_name = table_name
        self.cluster_id = cluster_id
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name)
    
    def acquire_lock(self, timeout: int = 5) -> bool:
        """
        Acquire distributed lock using DynamoDB conditional writes
        
        Args:
            timeout: Maximum seconds to wait for lock
        
        Returns:
            True if lock acquired, False otherwise
        """
        try:
            # Try to acquire lock with conditional expression
            self.table.update_item(
                Key={'cluster_id': self.cluster_id},
                UpdateExpression='SET scaling_in_progress = :true, lock_acquired_at = :now',
                ConditionExpression='attribute_not_exists(scaling_in_progress) OR scaling_in_progress = :false',
                ExpressionAttributeValues={
                    ':true': True,
                    ':false': False,
                    ':now': int(time.time())
                }
            )
            logger.info(f"Lock acquired for cluster {self.cluster_id}")
            return True
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                logger.warning(f"Lock already held for cluster {self.cluster_id}")
                return False
            else:
                logger.error(f"Error acquiring lock: {str(e)}")
                raise
    
    def release_lock(self):
        """Release the distributed lock"""
        try:
            self.table.update_item(
                Key={'cluster_id': self.cluster_id},
                UpdateExpression='SET scaling_in_progress = :false, lock_released_at = :now',
                ExpressionAttributeValues={
                    ':false': False,
                    ':now': int(time.time())
                }
            )
            logger.info(f"Lock released for cluster {self.cluster_id}")
            
        except ClientError as e:
            logger.error(f"Error releasing lock: {str(e)}")
            raise
    
    def get_state(self) -> Dict:
        """Get current cluster state from DynamoDB"""
        try:
            response = self.table.get_item(Key={'cluster_id': self.cluster_id})
            
            if 'Item' in response:
                return response['Item']
            else:
                # Return default state if item doesn't exist
                logger.info(f"No state found for {self.cluster_id}, returning defaults")
                return {
                    'cluster_id': self.cluster_id,
                    'node_count': 2,
                    'last_scale_time': 0,
                    'scaling_in_progress': False
                }
                
        except ClientError as e:
            logger.error(f"Error getting state: {str(e)}")
            raise
    
    def update_state(self, new_node_count: int):
        """Update cluster state after scaling operation"""
        try:
            self.table.update_item(
                Key={'cluster_id': self.cluster_id},
                UpdateExpression='SET node_count = :count, last_scale_time = :time',
                ExpressionAttributeValues={
                    ':count': new_node_count,
                    ':time': int(time.time())
                }
            )
            logger.info(f"State updated: node_count={new_node_count}")
            
        except ClientError as e:
            logger.error(f"Error updating state: {str(e)}")
            raise
