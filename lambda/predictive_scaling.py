"""
Predictive scaling module using historical metrics patterns.
Analyzes past load trends to proactively scale before traffic spikes.
"""

import boto3
import logging
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
from collections import defaultdict
import statistics

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class PredictiveScaler:
    """Analyzes historical metrics to predict future resource needs"""
    
    def __init__(self, table_name: str, lookback_days: int = 7):
        """
        Initialize predictive scaler
        
        Args:
            table_name: DynamoDB table for storing historical metrics
            lookback_days: Number of days to analyze for patterns
        """
        self.table_name = table_name
        self.lookback_days = lookback_days
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(table_name)
    
    def store_metrics(self, timestamp: datetime, cpu_percent: float, 
                     memory_percent: float, pending_pods: int, node_count: int) -> bool:
        """
        Store current metrics for historical analysis
        
        Args:
            timestamp: Metric collection timestamp
            cpu_percent: Current CPU utilization percentage
            memory_percent: Current memory utilization percentage
            pending_pods: Number of pending pods
            node_count: Current node count
        
        Returns:
            True if stored successfully
        """
        try:
            item = {
                'timestamp': timestamp.isoformat(),
                'hour': timestamp.hour,
                'day_of_week': timestamp.weekday(),  # 0=Monday, 6=Sunday
                'cpu_percent': str(cpu_percent),
                'memory_percent': str(memory_percent),
                'pending_pods': pending_pods,
                'node_count': node_count,
                'ttl': int((timestamp + timedelta(days=30)).timestamp())  # Expire after 30 days
            }
            
            self.table.put_item(Item=item)
            logger.debug(f"Stored metrics for {timestamp.isoformat()}")
            return True
            
        except Exception as e:
            logger.error(f"Error storing metrics: {e}")
            return False
    
    def get_historical_metrics(self, days: int = None) -> List[Dict]:
        """
        Retrieve historical metrics from DynamoDB
        
        Args:
            days: Number of days to look back (default: self.lookback_days)
        
        Returns:
            List of metric dictionaries
        """
        if days is None:
            days = self.lookback_days
        
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)
            
            # Scan table for recent metrics (in production, use GSI with timestamp)
            response = self.table.scan(
                FilterExpression='#ts >= :cutoff',
                ExpressionAttributeNames={'#ts': 'timestamp'},
                ExpressionAttributeValues={':cutoff': cutoff.isoformat()}
            )
            
            metrics = response.get('Items', [])
            
            # Convert string values back to numbers
            for metric in metrics:
                metric['cpu_percent'] = float(metric['cpu_percent'])
                metric['memory_percent'] = float(metric['memory_percent'])
            
            logger.info(f"Retrieved {len(metrics)} historical metrics")
            return metrics
            
        except Exception as e:
            logger.error(f"Error retrieving historical metrics: {e}")
            return []
    
    def detect_hourly_patterns(self, metrics: List[Dict]) -> Dict[int, Dict]:
        """
        Detect load patterns by hour of day
        
        Args:
            metrics: Historical metrics data
        
        Returns:
            Dict mapping hour (0-23) to average metrics for that hour
        """
        hourly_data = defaultdict(lambda: {'cpu': [], 'memory': [], 'pending': []})
        
        for metric in metrics:
            hour = metric['hour']
            hourly_data[hour]['cpu'].append(metric['cpu_percent'])
            hourly_data[hour]['memory'].append(metric['memory_percent'])
            hourly_data[hour]['pending'].append(metric['pending_pods'])
        
        # Calculate averages and standard deviations
        patterns = {}
        for hour in range(24):
            if hourly_data[hour]['cpu']:
                patterns[hour] = {
                    'avg_cpu': statistics.mean(hourly_data[hour]['cpu']),
                    'avg_memory': statistics.mean(hourly_data[hour]['memory']),
                    'avg_pending': statistics.mean(hourly_data[hour]['pending']),
                    'cpu_stddev': statistics.stdev(hourly_data[hour]['cpu']) if len(hourly_data[hour]['cpu']) > 1 else 0,
                    'sample_count': len(hourly_data[hour]['cpu'])
                }
        
        return patterns
    
    def detect_weekly_patterns(self, metrics: List[Dict]) -> Dict[int, Dict]:
        """
        Detect load patterns by day of week
        
        Args:
            metrics: Historical metrics data
        
        Returns:
            Dict mapping day (0=Mon, 6=Sun) to average metrics
        """
        daily_data = defaultdict(lambda: {'cpu': [], 'memory': [], 'pending': []})
        
        for metric in metrics:
            day = metric['day_of_week']
            daily_data[day]['cpu'].append(metric['cpu_percent'])
            daily_data[day]['memory'].append(metric['memory_percent'])
            daily_data[day]['pending'].append(metric['pending_pods'])
        
        patterns = {}
        for day in range(7):
            if daily_data[day]['cpu']:
                patterns[day] = {
                    'avg_cpu': statistics.mean(daily_data[day]['cpu']),
                    'avg_memory': statistics.mean(daily_data[day]['memory']),
                    'avg_pending': statistics.mean(daily_data[day]['pending']),
                    'sample_count': len(daily_data[day]['cpu'])
                }
        
        return patterns
    
    def predict_next_hour_load(self, current_metrics: Dict) -> Optional[Dict]:
        """
        Predict resource requirements for the next hour
        
        Args:
            current_metrics: Current cluster metrics
        
        Returns:
            Predicted metrics for next hour, or None if insufficient data
        """
        try:
            # Get historical data
            historical = self.get_historical_metrics()
            
            if len(historical) < 20:  # Need minimum data points
                logger.warning("Insufficient historical data for predictions")
                return None
            
            # Analyze patterns
            hourly_patterns = self.detect_hourly_patterns(historical)
            weekly_patterns = self.detect_weekly_patterns(historical)
            
            # Get current time context
            now = datetime.utcnow()
            next_hour = (now.hour + 1) % 24
            current_day = now.weekday()
            
            # Predict based on historical patterns
            prediction = {
                'predicted_cpu': 0,
                'predicted_memory': 0,
                'predicted_pending_pods': 0,
                'confidence': 0
            }
            
            # Use hourly pattern if available
            if next_hour in hourly_patterns:
                pattern = hourly_patterns[next_hour]
                if pattern['sample_count'] >= 3:  # Need at least 3 samples
                    prediction['predicted_cpu'] = pattern['avg_cpu']
                    prediction['predicted_memory'] = pattern['avg_memory']
                    prediction['predicted_pending_pods'] = int(pattern['avg_pending'])
                    prediction['confidence'] = min(pattern['sample_count'] / 10.0, 1.0)
            
            # Apply weekly trend modifier
            if current_day in weekly_patterns:
                weekly = weekly_patterns[current_day]
                overall_avg_cpu = statistics.mean([m['cpu_percent'] for m in historical])
                
                # Scale prediction by weekly trend
                if overall_avg_cpu > 0:
                    weekly_multiplier = weekly['avg_cpu'] / overall_avg_cpu
                    prediction['predicted_cpu'] *= weekly_multiplier
                    prediction['predicted_memory'] *= weekly_multiplier
            
            # Add safety margin for uncertainty
            if prediction['confidence'] < 0.5:
                prediction['predicted_cpu'] *= 1.2
                prediction['predicted_memory'] *= 1.2
            
            logger.info(f"Prediction for hour {next_hour}: CPU={prediction['predicted_cpu']:.1f}%, "
                       f"Memory={prediction['predicted_memory']:.1f}%, "
                       f"Confidence={prediction['confidence']:.2f}")
            
            return prediction
            
        except Exception as e:
            logger.error(f"Error predicting load: {e}")
            return None
    
    def should_proactive_scale_up(self, current_metrics: Dict, 
                                   prediction: Dict, threshold: float = 70.0) -> Tuple[bool, str]:
        """
        Determine if proactive scale-up is needed
        
        Args:
            current_metrics: Current cluster metrics
            prediction: Predicted metrics from predict_next_hour_load()
            threshold: CPU/memory threshold for scaling
        
        Returns:
            Tuple of (should_scale, reason)
        """
        if not prediction or prediction['confidence'] < 0.3:
            return False, "Insufficient prediction confidence"
        
        # Check if predicted load exceeds threshold
        if prediction['predicted_cpu'] > threshold:
            return True, f"Predicted CPU spike: {prediction['predicted_cpu']:.1f}% (threshold: {threshold}%)"
        
        if prediction['predicted_memory'] > threshold * 1.05:  # Slightly higher threshold for memory
            return True, f"Predicted memory spike: {prediction['predicted_memory']:.1f}%"
        
        if prediction['predicted_pending_pods'] > 0:
            return True, f"Predicted pending pods: {prediction['predicted_pending_pods']}"
        
        return False, "No predicted load spike"
    
    def calculate_recommended_nodes(self, prediction: Dict, 
                                    current_nodes: int, 
                                    target_utilization: float = 60.0) -> int:
        """
        Calculate recommended node count based on prediction
        
        Args:
            prediction: Predicted metrics
            prediction: Predicted load metrics
            current_nodes: Current number of nodes
            target_utilization: Target CPU utilization percentage
        
        Returns:
            Recommended node count
        """
        if not prediction:
            return current_nodes
        
        predicted_cpu = prediction['predicted_cpu']
        
        # Calculate nodes needed to maintain target utilization
        # predicted_cpu is cluster-wide average, so we scale proportionally
        if target_utilization > 0:
            recommended = max(
                current_nodes,
                int((predicted_cpu / target_utilization) * current_nodes) + 1
            )
        else:
            recommended = current_nodes
        
        # Limit recommendation to reasonable bounds
        recommended = max(2, min(recommended, current_nodes + 3))  # Don't add more than 3 at once
        
        logger.info(f"Recommended nodes: {recommended} (current: {current_nodes}, "
                   f"predicted CPU: {predicted_cpu:.1f}%)")
        
        return recommended
