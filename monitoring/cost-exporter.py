"""
Prometheus custom exporter for AWS EC2 cost metrics
Calculates real-time cost based on running instances
"""

import time
import boto3
from prometheus_client import start_http_server, Gauge
from typing import Dict, List

# Prometheus metrics
ec2_cost_per_hour = Gauge(
    'aws_ec2_instance_cost_per_hour',
    'EC2 instance cost per hour',
    ['instance_id', 'instance_type', 'lifecycle', 'cluster']
)

# EC2 pricing (us-east-1, on-demand)
EC2_PRICING = {
    't3.small': 0.0208,
    't3.medium': 0.0416,
    't3.large': 0.0832,
    't3.xlarge': 0.1664,
    't3.2xlarge': 0.3328,
}

# Spot pricing is approximately 60-70% cheaper
SPOT_DISCOUNT = 0.65


class EC2CostExporter:
    """Export EC2 instance costs to Prometheus"""
    
    def __init__(self, cluster_name: str = 'node-fleet', region: str = 'ap-south-1'):
        self.cluster_name = cluster_name
        self.region = region
        self.ec2_client = boto3.client('ec2', region_name=region)
    
    def get_running_instances(self) -> List[Dict]:
        """Get all running instances for the cluster"""
        response = self.ec2_client.describe_instances(
            Filters=[
                {'Name': 'tag:Project', 'Values': [self.cluster_name]},
                {'Name': 'instance-state-name', 'Values': ['running']}
            ]
        )
        
        instances = []
        for reservation in response['Reservations']:
            instances.extend(reservation['Instances'])
        
        return instances
    
    def calculate_instance_cost(self, instance: Dict) -> float:
        """Calculate hourly cost for an instance"""
        instance_type = instance.get('InstanceType', 't3.medium')
        lifecycle = instance.get('InstanceLifecycle', 'on-demand')
        
        # Get base price
        base_price = EC2_PRICING.get(instance_type, 0.0416)
        
        # Apply spot discount
        if lifecycle == 'spot':
            return base_price * SPOT_DISCOUNT
        
        return base_price
    
    def update_metrics(self):
        """Update Prometheus metrics with current costs"""
        instances = self.get_running_instances()
        
        # Track which instance IDs we've seen
        seen_instances = set()
        
        for instance in instances:
            instance_id = instance['InstanceId']
            instance_type = instance.get('InstanceType', 'unknown')
            lifecycle = instance.get('InstanceLifecycle', 'on-demand')
            
            cost = self.calculate_instance_cost(instance)
            
            ec2_cost_per_hour.labels(
                instance_id=instance_id,
                instance_type=instance_type,
                lifecycle=lifecycle,
                cluster=self.cluster_name
            ).set(cost)
            
            seen_instances.add(instance_id)
        
        print(f"Updated metrics for {len(instances)} instances. Total hourly cost: ${sum(self.calculate_instance_cost(i) for i in instances):.4f}")
    
    def run(self, interval: int = 15):
        """Run exporter with periodic updates"""
        print(f"Starting EC2 Cost Exporter for cluster '{self.cluster_name}'")
        print(f"Metrics will update every {interval} seconds")
        
        while True:
            try:
                self.update_metrics()
            except Exception as e:
                print(f"Error updating metrics: {e}")
            
            time.sleep(interval)


if __name__ == '__main__':
    # Start Prometheus HTTP server on port 9100
    start_http_server(9100)
    print("Prometheus metrics available at http://localhost:9100/metrics")
    
    # Run exporter
    exporter = EC2CostExporter(
        cluster_name='node-fleet',
        region='ap-south-1'
    )
    exporter.run(interval=15)  # Update every 15 seconds
