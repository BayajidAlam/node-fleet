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
            lock_expiry_time = current_time + 360  # Lock expires after 6 minutes (covers worst-case drain + join)
            expired_time = current_time  # Locks older than now are considered expired
            
            # Try to acquire lock with conditional expression
            # Lock can be acquired if:
            # 1. No lock exists (attribute_not_exists)
            # 2. Lock is released (scaling_in_progress = false)
            # 3. Lock has expired (lock_acquired_at < current_time - 360 seconds)
            self.table.update_item(
                Key={'cluster_id': self.cluster_id},
                UpdateExpression='SET scaling_in_progress = :true, lock_acquired_at = :now, lock_expiry = :expiry',
                ConditionExpression='attribute_not_exists(scaling_in_progress) OR scaling_in_progress = :false OR lock_acquired_at < :expired',
                ExpressionAttributeValues={
                    ':true': True,
                    ':false': False,
                    ':now': current_time,
                    ':expiry': lock_expiry_time,
                    ':expired': current_time - 360  # Locks older than 6 minutes are stale
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
                    if lock_age > 360:
                        logger.warning(f"Stale lock detected (age: {lock_age}s), forcing release")
                        self.release_lock()
                        return self.acquire_lock(timeout)  # Retry once
                    else:
                        logger.warning(f"Lock already held for cluster {self.cluster_id} (age: {lock_age}s)")
                        return False
                except Exception:
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
                state = response['Item']
                # Ensure metrics_history exists
                if 'metrics_history' not in state:
                    state['metrics_history'] = []
                # Convert Decimals back to floats/ints
                return state
            else:
                # Return default state if item doesn't exist
                logger.info(f"No state found for {self.cluster_id}, returning defaults")
                return {
                    'cluster_id': self.cluster_id,
                    'node_count': 2,
                    'last_scale_time': 0,
                    'scaling_in_progress': False,
                    'metrics_history': []
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

    def update_metrics_history(self, current_metrics: Dict, max_history: int = 10):
        """
        Update metrics history in DynamoDB (sliding window)
        
        Args:
            current_metrics: Current cluster metrics
            max_history: Max number of history items to keep
        """
        try:
            # Prepare metric snapshot with timestamp
            from decimal import Decimal
            snapshot = {
                'timestamp': int(time.time()),
                'cpu_usage': Decimal(str(current_metrics.get('cpu_usage', 0))),
                'memory_usage': Decimal(str(current_metrics.get('memory_usage', 0))),
                'pending_pods': int(current_metrics.get('pending_pods', 0))
            }
            
            # Use list_append and if_not_exists for atomic update
            # Note: Removal of old items is done after append to maintain window size
            self.table.update_item(
                Key={'cluster_id': self.cluster_id},
                UpdateExpression='SET metrics_history = list_append(if_not_exists(metrics_history, :empty_list), :new_metric)',
                ExpressionAttributeValues={
                    ':new_metric': [snapshot],
                    ':empty_list': []
                }
            )
            
            # Trim history if it exceeds max_history
            state = self.get_state()
            history = state.get('metrics_history', [])
            if len(history) > max_history:
                # Remove oldest items (DynamoDB doesn't have a direct "trim" so we update the whole list)
                trimmed_history = history[-max_history:]
                self.table.update_item(
                    Key={'cluster_id': self.cluster_id},
                    UpdateExpression='SET metrics_history = :trimmed',
                    ExpressionAttributeValues={
                        ':trimmed': trimmed_history
                    }
                )
                
            logger.info("Metrics history updated in DynamoDB")
            
        except ClientError as e:
            logger.error(f"Error updating metrics history: {str(e)}")
            # Don't raise - metric history failure shouldn't kill the autoscaler

    def store_drain_state(self, draining_instances: list):
        """
        Persist async drain state so the next Lambda invocation can complete termination.
        draining_instances: list of dicts with keys: instance_id, node_name, command_id,
                            master_instance_id, start_time
        """
        try:
            from decimal import Decimal
            # Convert timestamps to Decimal for DynamoDB
            items = []
            for d in draining_instances:
                items.append({
                    'instance_id': d['instance_id'],
                    'node_name': d['node_name'],
                    'command_id': d['command_id'],
                    'master_instance_id': d.get('master_instance_id', ''),
                    'start_time': Decimal(str(d.get('start_time', 0))),
                })
            self.table.update_item(
                Key={'cluster_id': self.cluster_id},
                UpdateExpression='SET draining_instances = :items',
                ExpressionAttributeValues={':items': items}
            )
            logger.info(f"Stored drain state for {len(items)} instance(s)")
        except ClientError as e:
            logger.error(f"Error storing drain state: {str(e)}")

    def get_pending_drains(self) -> list:
        """Return list of instances currently being drained (pending termination)."""
        try:
            state = self.get_state()
            raw = state.get('draining_instances', [])
            result = []
            for d in raw:
                result.append({
                    'instance_id': str(d.get('instance_id', '')),
                    'node_name': str(d.get('node_name', '')),
                    'command_id': str(d.get('command_id', '')),
                    'master_instance_id': str(d.get('master_instance_id', '')),
                    'start_time': int(d.get('start_time', 0)),
                })
            return result
        except Exception as e:
            logger.error(f"Error reading pending drains: {e}")
            return []

    def clear_drain_instance(self, instance_id: str):
        """Remove a specific instance from the draining list after termination."""
        try:
            pending = self.get_pending_drains()
            updated = [d for d in pending if d['instance_id'] != instance_id]
            self.table.update_item(
                Key={'cluster_id': self.cluster_id},
                UpdateExpression='SET draining_instances = :items',
                ExpressionAttributeValues={':items': updated}
            )
            logger.info(f"Cleared drain state for {instance_id}")
        except ClientError as e:
            logger.error(f"Error clearing drain state for {instance_id}: {str(e)}")

    def store_pending_scale_up(self, instance_ids: list, launch_time: int):
        """Store newly launched instance IDs pending node-Ready verification."""
        try:
            from decimal import Decimal
            items = [{'instance_id': iid, 'launch_time': Decimal(str(launch_time))} for iid in instance_ids]
            self.table.update_item(
                Key={'cluster_id': self.cluster_id},
                UpdateExpression='SET pending_scale_up = :items',
                ExpressionAttributeValues={':items': items}
            )
            logger.info(f"Stored {len(items)} pending scale-up instance(s)")
        except ClientError as e:
            logger.error(f"Error storing pending scale-up: {str(e)}")

    def get_pending_scale_ups(self) -> list:
        """Return list of launched instances not yet verified as Ready."""
        try:
            state = self.get_state()
            raw = state.get('pending_scale_up', [])
            return [{'instance_id': str(d['instance_id']), 'launch_time': int(d.get('launch_time', 0))} for d in raw]
        except Exception as e:
            logger.error(f"Error reading pending scale-ups: {e}")
            return []

    def clear_pending_scale_up(self, instance_id: str):
        """Remove an instance from the pending scale-up list once it's confirmed Ready."""
        try:
            pending = self.get_pending_scale_ups()
            updated = [d for d in pending if d['instance_id'] != instance_id]
            self.table.update_item(
                Key={'cluster_id': self.cluster_id},
                UpdateExpression='SET pending_scale_up = :items',
                ExpressionAttributeValues={':items': updated}
            )
            logger.info(f"Cleared pending scale-up for {instance_id}")
        except ClientError as e:
            logger.error(f"Error clearing pending scale-up for {instance_id}: {str(e)}")
