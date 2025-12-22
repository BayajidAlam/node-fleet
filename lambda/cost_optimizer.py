"""
Cost optimization recommendations for autoscaler
Analyzes cluster usage patterns and suggests cost-saving opportunities
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cloudwatch = boto3.client('cloudwatch')
ec2 = boto3.client('ec2')


class CostOptimizer:
    """Analyzes cluster metrics and provides cost optimization recommendations"""
    
    def __init__(self, cluster_id: str):
        self.cluster_id = cluster_id
    
    def analyze_and_recommend(self, current_metrics: Dict, current_nodes: int) -> Dict:
        """
        Analyze cluster usage and provide cost optimization recommendations
        
        Args:
            current_metrics: Current cluster metrics
            current_nodes: Current node count
        
        Returns:
            Dict with recommendations and potential savings
        """
        recommendations = []
        potential_savings_percent = 0
        
        # Check for underutilization
        underutil_rec = self._check_underutilization(current_metrics, current_nodes)
        if underutil_rec:
            recommendations.append(underutil_rec)
            potential_savings_percent += underutil_rec.get('savings_percent', 0)
        
        # Check spot instance usage
        spot_rec = self._check_spot_usage()
        if spot_rec:
            recommendations.append(spot_rec)
            potential_savings_percent += spot_rec.get('savings_percent', 0)
        
        # Check for rightsizing opportunities
        rightsize_rec = self._check_instance_rightsizing(current_metrics)
        if rightsize_rec:
            recommendations.append(rightsize_rec)
            potential_savings_percent += rightsize_rec.get('savings_percent', 0)
        
        # Check for idle time patterns
        idle_rec = self._check_idle_patterns()
        if idle_rec:
            recommendations.append(idle_rec)
            potential_savings_percent += idle_rec.get('savings_percent', 0)
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "cluster_id": self.cluster_id,
            "recommendations": recommendations,
            "potential_savings_percent": min(potential_savings_percent, 60),  # Cap at 60%
            "current_nodes": current_nodes,
        }
    
    def _check_underutilization(self, metrics: Dict, current_nodes: int) -> Optional[Dict]:
        """Check for sustained underutilization"""
        cpu = metrics.get('cpu_usage', 0)
        memory = metrics.get('memory_usage', 0)
        
        # Get average utilization over last 6 hours
        avg_cpu = self._get_average_metric('ClusterCPU', hours=6)
        avg_memory = self._get_average_metric('ClusterMemory', hours=6)
        
        if avg_cpu and avg_cpu < 20 and avg_memory and avg_memory < 30:
            # Cluster is significantly underutilized
            recommended_nodes = max(2, int(current_nodes * 0.7))
            savings = ((current_nodes - recommended_nodes) / current_nodes) * 100
            
            return {
                "type": "underutilization",
                "severity": "high" if savings > 20 else "medium",
                "message": f"Cluster running at {avg_cpu:.1f}% CPU, {avg_memory:.1f}% memory over 6h",
                "action": f"Consider reducing min_nodes from {current_nodes} to {recommended_nodes}",
                "savings_percent": savings,
                "impact": "May increase scale-up latency during traffic spikes"
            }
        
        return None
    
    def _check_spot_usage(self) -> Optional[Dict]:
        """Check spot instance adoption rate"""
        try:
            # Get worker instances
            response = ec2.describe_instances(
                Filters=[
                    {'Name': 'tag:Role', 'Values': ['k3s-worker']},
                    {'Name': 'instance-state-name', 'Values': ['running']}
                ]
            )
            
            total = 0
            spot_count = 0
            
            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    total += 1
                    if instance.get('InstanceLifecycle') == 'spot':
                        spot_count += 1
            
            if total == 0:
                return None
            
            spot_percentage = (spot_count / total) * 100
            
            if spot_percentage < 60:
                # Low spot usage
                target_spot = 70
                additional_savings = (target_spot - spot_percentage) * 0.65  # Spot saves ~65%
                
                return {
                    "type": "spot_instances",
                    "severity": "medium",
                    "message": f"Only {spot_percentage:.1f}% of workers are spot instances",
                    "action": f"Increase SPOT_PERCENTAGE to {target_spot}% for better cost efficiency",
                    "savings_percent": additional_savings,
                    "impact": "Increased interruption risk (mitigated by auto-replacement)"
                }
            
        except Exception as e:
            logger.error(f"Error checking spot usage: {e}")
        
        return None
    
    def _check_instance_rightsizing(self, metrics: Dict) -> Optional[Dict]:
        """Check if instance types are appropriately sized"""
        # This is a simplified version - in production, analyze actual resource requests
        cpu = metrics.get('cpu_usage', 0)
        memory = metrics.get('memory_usage', 0)
        
        # If both CPU and memory are consistently low, instances might be oversized
        if cpu < 15 and memory < 20:
            return {
                "type": "rightsizing",
                "severity": "low",
                "message": f"Very low resource usage: CPU {cpu:.1f}%, Memory {memory:.1f}%",
                "action": "Consider using smaller instance types (e.g., t3.medium → t3.small)",
                "savings_percent": 15,
                "impact": "May need more nodes, but total cost could be lower"
            }
        
        return None
    
    def _check_idle_patterns(self) -> Optional[Dict]:
        """Check for consistent idle periods (e.g., nights/weekends)"""
        # Get hourly CPU data for last 7 days
        try:
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(days=7)
            
            response = cloudwatch.get_metric_statistics(
                Namespace='NodeFleet/Autoscaler',
                MetricName='ClusterCPU',
                Dimensions=[{'Name': 'ClusterID', 'Value': self.cluster_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,  # 1 hour
                Statistics=['Average']
            )
            
            if len(response['Datapoints']) < 24:
                return None  # Not enough data
            
            # Group by hour of day
            hourly_averages = {}
            for dp in response['Datapoints']:
                hour = dp['Timestamp'].hour
                if hour not in hourly_averages:
                    hourly_averages[hour] = []
                hourly_averages[hour].append(dp['Average'])
            
            # Find idle hours (consistently < 10% CPU)
            idle_hours = []
            for hour, values in hourly_averages.items():
                avg = sum(values) / len(values)
                if avg < 10 and len(values) >= 4:  # At least 4 data points
                    idle_hours.append(hour)
            
            if len(idle_hours) >= 6:
                # Significant idle period detected
                return {
                    "type": "idle_pattern",
                    "severity": "low",
                    "message": f"Detected {len(idle_hours)} consistently idle hours per day",
                    "action": f"Idle hours: {sorted(idle_hours)}. Consider scheduled scale-down or dev environment shutdown.",
                    "savings_percent": 10,
                    "impact": "Requires scheduled automation (e.g., Lambda to scale min_nodes)"
                }
            
        except Exception as e:
            logger.error(f"Error analyzing idle patterns: {e}")
        
        return None
    
    def _get_average_metric(self, metric_name: str, hours: int = 6) -> Optional[float]:
        """Get average value of a metric over specified hours"""
        try:
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(hours=hours)
            
            response = cloudwatch.get_metric_statistics(
                Namespace='NodeFleet/Autoscaler',
                MetricName=metric_name,
                Dimensions=[{'Name': 'ClusterID', 'Value': self.cluster_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,  # 1 hour intervals
                Statistics=['Average']
            )
            
            if not response['Datapoints']:
                return None
            
            values = [dp['Average'] for dp in response['Datapoints']]
            return sum(values) / len(values)
            
        except Exception as e:
            logger.error(f"Error getting metric average: {e}")
            return None


def get_cost_recommendations(cluster_id: str, current_metrics: Dict, current_nodes: int) -> Dict:
    """
    Convenience function to get cost optimization recommendations
    
    Args:
        cluster_id: Cluster identifier
        current_metrics: Current cluster metrics
        current_nodes: Current node count
    
    Returns:
        Cost optimization recommendations
    """
    optimizer = CostOptimizer(cluster_id)
    return optimizer.analyze_and_recommend(current_metrics, current_nodes)
