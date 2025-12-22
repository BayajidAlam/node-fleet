"""
Cost Optimization Recommender

Analyzes cluster cost metrics and provides actionable optimization recommendations.
"""

import boto3
from typing import List, Dict, Tuple
from datetime import datetime, timedelta


class CostOptimizationRecommender:
    """Generate cost optimization recommendations"""
    
    def __init__(self, cluster_name: str = 'node-fleet', region: str = 'ap-southeast-1'):
        self.cluster_name = cluster_name
        self.region = region
        self.ec2_client = boto3.client('ec2', region_name=region)
        self.cloudwatch = boto3.client('cloudwatch', region_name=region)
    
    def get_instance_utilization(self, instance_id: str) -> Tuple[float, float]:
        """
        Get CPU and memory utilization for an instance
        Returns: (cpu_percent, memory_percent)
        """
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=24)
        
        # Get CPU utilization from CloudWatch
        try:
            response = self.cloudwatch.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName='CPUUtilization',
                Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,  # 1 hour
                Statistics=['Average']
            )
            
            if response['Datapoints']:
                cpu_avg = sum(dp['Average'] for dp in response['Datapoints']) / len(response['Datapoints'])
            else:
                cpu_avg = 0
            
            # Memory would need CloudWatch agent - simulate for now
            memory_avg = cpu_avg * 0.8  # Rough estimate
            
            return cpu_avg, memory_avg
        
        except Exception as e:
            print(f"Error getting metrics for {instance_id}: {e}")
            return 0, 0
    
    def analyze_underutilized_instances(self) -> List[Dict]:
        """Find underutilized instances that could be downsized"""
        recommendations = []
        
        response = self.ec2_client.describe_instances(
            Filters=[
                {'Name': 'tag:Project', 'Values': [self.cluster_name]},
                {'Name': 'instance-state-name', 'Values': ['running']}
            ]
        )
        
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                instance_id = instance['InstanceId']
                instance_type = instance.get('InstanceType', 'unknown')
                
                cpu_util, mem_util = self.get_instance_utilization(instance_id)
                
                if cpu_util < 20 and mem_util < 30:
                    recommendations.append({
                        'type': 'downsize_instance',
                        'severity': 'high',
                        'instance_id': instance_id,
                        'current_type': instance_type,
                        'cpu_utilization': cpu_util,
                        'memory_utilization': mem_util,
                        'recommendation': f'Consider downsizing {instance_type} (CPU: {cpu_util:.1f}%, Memory: {mem_util:.1f}%)',
                        'potential_savings': self._estimate_downsize_savings(instance_type)
                    })
        
        return recommendations
    
    def analyze_spot_opportunities(self) -> List[Dict]:
        """Find on-demand instances that could be converted to spot"""
        recommendations = []
        
        response = self.ec2_client.describe_instances(
            Filters=[
                {'Name': 'tag:Project', 'Values': [self.cluster_name]},
                {'Name': 'instance-state-name', 'Values': ['running']}
            ]
        )
        
        spot_count = 0
        ondemand_count = 0
        
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                lifecycle = instance.get('InstanceLifecycle', 'on-demand')
                if lifecycle == 'spot':
                    spot_count += 1
                else:
                    ondemand_count += 1
        
        total = spot_count + ondemand_count
        spot_percentage = (spot_count / total * 100) if total > 0 else 0
        
        if spot_percentage < 60:  # Target 70% spot
            additional_spot = int((total * 0.7) - spot_count)
            if additional_spot > 0:
                recommendations.append({
                    'type': 'increase_spot_usage',
                    'severity': 'medium',
                    'current_spot_percentage': spot_percentage,
                    'target_spot_percentage': 70,
                    'additional_spot_instances': additional_spot,
                    'recommendation': f'Convert {additional_spot} more instances to spot (current: {spot_percentage:.1f}%, target: 70%)',
                    'potential_savings': additional_spot * 0.03 * 24 * 30  # ~$0.03/hr average savings
                })
        
        return recommendations
    
    def analyze_idle_times(self) -> List[Dict]:
        """Identify potential scheduling opportunities"""
        recommendations = []
        
        # This would analyze historical load patterns
        # For now, provide general recommendation
        recommendations.append({
            'type': 'schedule_optimization',
            'severity': 'low',
            'recommendation': 'Consider implementing scheduled scaling for known low-traffic periods',
            'potential_savings': 50  # Monthly estimate
        })
        
        return recommendations
    
    def analyze_reserved_instance_opportunities(self) -> List[Dict]:
        """Analyze if Reserved Instances would provide savings"""
        recommendations = []
        
        response = self.ec2_client.describe_instances(
            Filters=[
                {'Name': 'tag:Project', 'Values': [self.cluster_name]},
                {'Name': 'instance-state-name', 'Values': ['running']}
            ]
        )
        
        # Count stable on-demand instances
        stable_instances = {}
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                if instance.get('InstanceLifecycle', 'on-demand') == 'on-demand':
                    instance_type = instance.get('InstanceType', 'unknown')
                    stable_instances[instance_type] = stable_instances.get(instance_type, 0) + 1
        
        # If we have 2+ of same type running 24/7, recommend RI
        for instance_type, count in stable_instances.items():
            if count >= 2:
                recommendations.append({
                    'type': 'reserved_instance',
                    'severity': 'low',
                    'instance_type': instance_type,
                    'count': count,
                    'recommendation': f'Consider 1-year Reserved Instance for {count}x {instance_type} (saves ~30%)',
                    'potential_savings': count * 0.05 * 24 * 30 * 0.3  # 30% savings estimate
                })
        
        return recommendations
    
    def analyze_multi_az_balance(self) -> List[Dict]:
        """Check if resources are balanced across AZs"""
        recommendations = []
        
        response = self.ec2_client.describe_instances(
            Filters=[
                {'Name': 'tag:Project', 'Values': [self.cluster_name]},
                {'Name': 'instance-state-name', 'Values': ['running']}
            ]
        )
        
        az_counts = {}
        for reservation in response['Reservations']:
            for instance in reservation['Instances']:
                az = instance['Placement'].get('AvailabilityZone', 'unknown')
                az_counts[az] = az_counts.get(az, 0) + 1
        
        if len(az_counts) > 1:
            counts = list(az_counts.values())
            max_count = max(counts)
            min_count = min(counts)
            
            if (max_count - min_count) / max_count > 0.3:  # 30% imbalance
                recommendations.append({
                    'type': 'az_balance',
                    'severity': 'low',
                    'az_distribution': az_counts,
                    'recommendation': f'Instance distribution is unbalanced across AZs. Consider rebalancing.',
                    'potential_savings': 0  # No direct cost savings, but better resilience
                })
        
        return recommendations
    
    def _estimate_downsize_savings(self, current_type: str) -> float:
        """Estimate monthly savings from downsizing"""
        # Simplified - would map to actual smaller instance type
        current_cost = {
            't3.medium': 0.0464,
            't3.large': 0.0928,
            't3.xlarge': 0.1856,
            't3.2xlarge': 0.3712
        }.get(current_type, 0.05)
        
        savings_per_hour = current_cost * 0.5  # Assume 50% savings
        return savings_per_hour * 24 * 30
    
    def generate_report(self) -> Dict:
        """Generate comprehensive cost optimization report"""
        all_recommendations = []
        
        all_recommendations.extend(self.analyze_underutilized_instances())
        all_recommendations.extend(self.analyze_spot_opportunities())
        all_recommendations.extend(self.analyze_idle_times())
        all_recommendations.extend(self.analyze_reserved_instance_opportunities())
        all_recommendations.extend(self.analyze_multi_az_balance())
        
        # Sort by potential savings
        all_recommendations.sort(key=lambda x: x.get('potential_savings', 0), reverse=True)
        
        total_potential_savings = sum(r.get('potential_savings', 0) for r in all_recommendations)
        
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'cluster': self.cluster_name,
            'total_recommendations': len(all_recommendations),
            'total_potential_monthly_savings': total_potential_savings,
            'recommendations': all_recommendations
        }


if __name__ == '__main__':
    recommender = CostOptimizationRecommender()
    report = recommender.generate_report()
    
    print("\n" + "="*60)
    print("COST OPTIMIZATION REPORT")
    print("="*60)
    print(f"Cluster: {report['cluster']}")
    print(f"Generated: {report['timestamp']}")
    print(f"Total Recommendations: {report['total_recommendations']}")
    print(f"Potential Monthly Savings: ${report['total_potential_monthly_savings']:.2f}")
    print("="*60)
    
    for i, rec in enumerate(report['recommendations'], 1):
        print(f"\n{i}. [{rec['severity'].upper()}] {rec['type']}")
        print(f"   {rec['recommendation']}")
        if rec.get('potential_savings'):
            print(f"   Potential Savings: ${rec['potential_savings']:.2f}/month")
