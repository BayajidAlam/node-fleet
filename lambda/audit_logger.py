"""
DynamoDB Stream processor for autoscaler audit trail
Logs all state changes to CloudWatch for compliance and debugging
"""

import json
import logging
from typing import Dict, Any
from datetime import datetime
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cloudwatch = boto3.client('logs')


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Process DynamoDB Stream events for audit logging
    
    Args:
        event: DynamoDB Stream event with records
        context: Lambda context
    
    Returns:
        Processing status
    """
    logger.info(f"Processing {len(event['Records'])} DynamoDB stream records")
    
    for record in event['Records']:
        try:
            process_record(record)
        except Exception as e:
            logger.error(f"Error processing record: {e}")
            # Don't fail the entire batch
            continue
    
    return {
        "statusCode": 200,
        "body": f"Processed {len(event['Records'])} records"
    }


def process_record(record: Dict) -> None:
    """
    Process a single DynamoDB Stream record
    
    Args:
        record: DynamoDB stream record
    """
    event_name = record['eventName']
    
    # Parse old and new images
    old_image = record['dynamodb'].get('OldImage', {})
    new_image = record['dynamodb'].get('NewImage', {})
    
    # Convert DynamoDB format to regular dict
    old_state = deserialize_dynamodb_item(old_image) if old_image else {}
    new_state = deserialize_dynamodb_item(new_image) if new_image else {}
    
    # Extract key information
    cluster_id = new_state.get('cluster_id') or old_state.get('cluster_id', 'unknown')
    timestamp = datetime.utcnow().isoformat()
    
    # Build audit log entry
    audit_entry = {
        "timestamp": timestamp,
        "cluster_id": cluster_id,
        "event_type": event_name,
        "old_state": old_state,
        "new_state": new_state,
    }
    
    # Log specific changes
    if event_name == "MODIFY":
        changes = detect_changes(old_state, new_state)
        audit_entry["changes"] = changes
        
        # Log scaling events
        if "node_count" in changes:
            logger.info(
                f"SCALING EVENT: Cluster {cluster_id} - "
                f"Nodes: {changes['node_count']['old']} → {changes['node_count']['new']}"
            )
        
        # Log lock acquisitions/releases
        if "scaling_in_progress" in changes:
            if changes["scaling_in_progress"]["new"]:
                logger.info(f"LOCK ACQUIRED: Cluster {cluster_id}")
            else:
                logger.info(f"LOCK RELEASED: Cluster {cluster_id}")
    
    elif event_name == "INSERT":
        logger.info(f"CLUSTER INITIALIZED: {cluster_id} with {new_state.get('node_count')} nodes")
    
    elif event_name == "REMOVE":
        logger.info(f"CLUSTER DELETED: {cluster_id}")
    
    # Write detailed audit log
    logger.info(f"AUDIT: {json.dumps(audit_entry, indent=2)}")


def deserialize_dynamodb_item(item: Dict) -> Dict:
    """
    Convert DynamoDB item format to regular Python dict
    
    Args:
        item: DynamoDB item with type descriptors
    
    Returns:
        Regular Python dictionary
    """
    result = {}
    
    for key, value_obj in item.items():
        # Extract value based on type
        if 'S' in value_obj:
            result[key] = value_obj['S']
        elif 'N' in value_obj:
            result[key] = float(value_obj['N'])
        elif 'BOOL' in value_obj:
            result[key] = value_obj['BOOL']
        elif 'NULL' in value_obj:
            result[key] = None
        elif 'M' in value_obj:
            result[key] = deserialize_dynamodb_item(value_obj['M'])
        elif 'L' in value_obj:
            result[key] = [deserialize_dynamodb_item({'item': v})['item'] for v in value_obj['L']]
    
    return result


def detect_changes(old: Dict, new: Dict) -> Dict:
    """
    Detect and document changes between old and new state
    
    Args:
        old: Old state
        new: New state
    
    Returns:
        Dictionary of changes
    """
    changes = {}
    
    # Check all keys
    all_keys = set(old.keys()) | set(new.keys())
    
    for key in all_keys:
        old_val = old.get(key)
        new_val = new.get(key)
        
        if old_val != new_val:
            changes[key] = {
                "old": old_val,
                "new": new_val
            }
    
    return changes
