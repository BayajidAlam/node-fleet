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
        Acquire distributed lock using DynamoDB conditional writes with expiry
        
        Args:
            timeout: Maximum seconds to wait for lock
        
        Returns:
            True if lock acquired, False otherwise
        """
        try:
            current_time = int(time.time())
            lock_expiry_time = current_time + 300  # Lock expires after 5 minutes
            expired_time = current_time  # Locks older than now are considered expired
            
            # Try to acquire lock with conditional expression
            # Lock can be acquired if:
            # 1. No lock exists (attribute_not_exists)
            # 2. Lock is released (scaling_in_progress = false)
            # 3. Lock has expired (lock_acquired_at < current_time - 300 seconds)
            self.table.update_item(
                Key={'cluster_id': self.cluster_id},
                UpdateExpression='SET scaling_in_progress = :true, lock_acquired_at = :now, lock_expiry = :expiry',
                ConditionExpression='attribute_not_exists(scaling_in_progress) OR scaling_in_progress = :false OR lock_acquired_at < :expired',
                ExpressionAttributeValues={
                    ':true': True,
                    ':false': False,
                    ':now': current_time,
                    ':expiry': lock_expiry_time,
                    ':expired': current_time - 300  # Locks older than 5 minutes
                }
            )
            logger.info(f"Lock acquired for cluster {self.cluster_id} (expires at {lock_expiry_time})")
            return True
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                # Check if lock is expired and force release
                try:
                    state = self.get_state()
                    lock_age = current_time - state.get('lock_acquired_at', current_time)
                    if lock_age > 300:
                        logger.warning(f"Stale lock detected (age: {lock_age}s), forcing release")
                        self.release_lock()
                        return self.acquire_lock(timeout)  # Retry once
                    else:
                        logger.warning(f"Lock already held for cluster {self.cluster_id} (age: {lock_age}s)")
                        return False
                except:
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
