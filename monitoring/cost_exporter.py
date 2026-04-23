"""
Enhanced Prometheus exporter for AWS cost metrics
Provides comprehensive cost tracking, optimization insights, and budget monitoring
"""

import time
import boto3
from datetime import datetime, timedelta
from prometheus_client import start_http_server, Gauge, Counter, Histogram
from typing import Dict, List, Tuple
import os

# Prometheus metrics - Instance costs
ec2_cost_per_hour = Gauge(
    'aws_ec2_instance_cost_per_hour',
    'EC2 instance cost per hour',
    ['instance_id', 'instance_type', 'lifecycle', 'availability_zone', 'cluster']
)

# Prometheus metrics - Aggregate costs
total_cluster_cost_hourly = Gauge(
    'aws_cluster_total_cost_hourly',
    'Total cluster hourly cost',
    ['cluster']
)

total_cluster_cost_daily = Gauge(
    'aws_cluster_total_cost_daily',
    'Estimated daily cluster cost',
    ['cluster']
)

total_cluster_cost_monthly = Gauge(
    'aws_cluster_total_cost_monthly',
    'Estimated monthly cluster cost',
    ['cluster']
)

# Cost breakdown by type
cost_by_lifecycle = Gauge(
    'aws_cost_by_lifecycle',
    'Cost breakdown by lifecycle (spot/on-demand)',
    ['lifecycle', 'cluster']
)

cost_by_instance_type = Gauge(
    'aws_cost_by_instance_type',
    'Cost breakdown by instance type',
    ['instance_type', 'cluster']
)

cost_by_az = Gauge(
    'aws_cost_by_availability_zone',
    'Cost breakdown by availability zone',
    ['availability_zone', 'cluster']
)

# Savings metrics
spot_savings_hourly = Gauge(
    'aws_spot_savings_hourly',
    'Hourly savings from using spot instances',
    ['cluster']
)

spot_savings_percentage = Gauge(
    'aws_spot_savings_percentage',
    'Percentage savings from spot instances',
    ['cluster']
)

autoscaling_savings_percentage = Gauge(
    'aws_autoscaling_savings_percentage',
    'Savings percentage vs static 5-node on-demand fleet baseline',
    ['cluster']
)

# Cost efficiency metrics
cost_per_pod = Gauge(
    'aws_cost_per_pod',
    'Average cost per running pod',
    ['cluster']
)

cost_per_cpu_core = Gauge(
    'aws_cost_per_cpu_core',
    'Cost per CPU core hour',
    ['cluster']
)

cost_per_gb_memory = Gauge(
    'aws_cost_per_gb_memory',
    'Cost per GB memory hour',
    ['cluster']
)

# Budget tracking
budget_usage_percentage = Gauge(
    'aws_budget_usage_percentage',
    'Monthly budget usage percentage',
    ['cluster']
)

budget_remaining_dollars = Gauge(
    'aws_budget_remaining_dollars',
    'Remaining monthly budget in dollars',
    ['cluster']
)

# Optimization opportunities
potential_savings_hourly = Gauge(
    'aws_potential_savings_hourly',
    'Potential hourly savings from optimization',
    ['optimization_type', 'cluster']
)

# Cost trend
cost_increase_rate = Gauge(
    'aws_cost_increase_rate',
    'Hourly cost increase/decrease rate',
    ['cluster']
)

# Scaling cost impact
scaling_events_total = Counter(
    'aws_scaling_events_total',
    'Total number of scaling events',
    ['direction', 'cluster']
)

scaling_cost_impact = Histogram(
    'aws_scaling_cost_impact_dollars',
    'Cost impact of scaling events',
    ['direction', 'cluster']
)

# EC2 pricing (ap-southeast-1, on-demand per hour)
EC2_PRICING = {
    # T3
    't3.micro': 0.0116,
    't3.small': 0.0232,
    't3.medium': 0.0464,
    't3.large': 0.0928,
    't3.xlarge': 0.1856,
    't3.2xlarge': 0.3712,
    # T3a
    't3a.micro': 0.0104,
    't3a.small': 0.0208,
    't3a.medium': 0.0416,
    't3a.large': 0.0832,
    't3a.xlarge': 0.1664,
    't3a.2xlarge': 0.3328,
    # T4g (Graviton2)
    't4g.micro': 0.0084,
    't4g.small': 0.0168,
    't4g.medium': 0.0336,
    't4g.large': 0.0672,
    't4g.xlarge': 0.1344,
    't4g.2xlarge': 0.2688,
    # M5
    'm5.large': 0.107,
    'm5.xlarge': 0.214,
    'm5.2xlarge': 0.428,
    # M5a
    'm5a.large': 0.096,
    'm5a.xlarge': 0.192,
    'm5a.2xlarge': 0.384,
    # M6i
    'm6i.large': 0.108,
    'm6i.xlarge': 0.216,
    'm6i.2xlarge': 0.432,
    # C5
    'c5.large': 0.094,
    'c5.xlarge': 0.188,
    'c5.2xlarge': 0.376,
    # C5a
    'c5a.large': 0.0848,
    'c5a.xlarge': 0.1696,
    'c5a.2xlarge': 0.3392,
    # C6i
    'c6i.large': 0.0952,
    'c6i.xlarge': 0.1904,
    'c6i.2xlarge': 0.3808,
    # R6i (memory-optimised)
    'r6i.large': 0.144,
    'r6i.xlarge': 0.288,
    'r6i.2xlarge': 0.576,
}

# Instance type specifications (vCPUs, Memory GB)
INSTANCE_SPECS = {
    't3.micro': (2, 1),
    't3.small': (2, 2),
    't3.medium': (2, 4),
    't3.large': (2, 8),
    't3.xlarge': (4, 16),
    't3.2xlarge': (8, 32),
    't3a.micro': (2, 1),
    't3a.small': (2, 2),
    't3a.medium': (2, 4),
    't3a.large': (2, 8),
    't3a.xlarge': (4, 16),
    't3a.2xlarge': (8, 32),
    't4g.micro': (2, 1),
    't4g.small': (2, 2),
    't4g.medium': (2, 4),
    't4g.large': (2, 8),
    't4g.xlarge': (4, 16),
    't4g.2xlarge': (8, 32),
    'm5.large': (2, 8),
    'm5.xlarge': (4, 16),
    'm5.2xlarge': (8, 32),
    'm5a.large': (2, 8),
    'm5a.xlarge': (4, 16),
    'm5a.2xlarge': (8, 32),
    'm6i.large': (2, 8),
    'm6i.xlarge': (4, 16),
    'm6i.2xlarge': (8, 32),
    'c5.large': (2, 4),
    'c5.xlarge': (4, 8),
    'c5.2xlarge': (8, 16),
    'c5a.large': (2, 4),
    'c5a.xlarge': (4, 8),
    'c5a.2xlarge': (8, 16),
    'c6i.large': (2, 4),
    'c6i.xlarge': (4, 8),
    'c6i.2xlarge': (8, 16),
    'r6i.large': (2, 16),
    'r6i.xlarge': (4, 32),
    'r6i.2xlarge': (8, 64),
}

# Spot pricing is approximately 60-70% cheaper
SPOT_DISCOUNT = 0.65


class EnhancedCostExporter:
    """Enhanced EC2 cost exporter with advanced analytics"""
    
    def __init__(
        self, 
        cluster_name: str = 'node-fleet', 
        region: str = 'ap-southeast-1',
        monthly_budget: float = 500.0
    ):
        self.cluster_name = cluster_name
        self.region = region
        self.monthly_budget = monthly_budget
        self.ec2_client = boto3.client('ec2', region_name=region)
        self.cloudwatch = boto3.client('cloudwatch', region_name=region)
        self.previous_cost = 0.0
        self.previous_timestamp = datetime.now()
    
    def get_running_instances(self) -> List[Dict]:
        """Get all running cluster instances — tries Project tag first, falls back to ManagedBy tag."""
        instances = []
        for filters in [
            [
                {'Name': 'tag:Project', 'Values': [self.cluster_name]},
                {'Name': 'instance-state-name', 'Values': ['running']},
            ],
            [
                {'Name': 'tag:ManagedBy', 'Values': ['autoscaler']},
                {'Name': 'instance-state-name', 'Values': ['running']},
            ],
        ]:
            response = self.ec2_client.describe_instances(Filters=filters)
            for reservation in response['Reservations']:
                instances.extend(reservation['Instances'])
            if instances:
                break
        return instances

    def get_pod_count(self) -> int:
        """Get actual running pod count from Kubernetes in-cluster API. Falls back to estimate."""
        try:
            from kubernetes import client, config
            config.load_incluster_config()
            v1 = client.CoreV1Api()
            pods = v1.list_pod_for_all_namespaces(field_selector="status.phase=Running")
            return len(pods.items)
        except Exception as e:
            instances = self.get_running_instances()
            estimated = len(instances) * 10
            print(f"Warning: K8s pod count failed ({e}), estimating {estimated}")
            return estimated
    
    def calculate_instance_cost(self, instance: Dict) -> float:
        """Calculate hourly cost for an instance"""
        instance_type = instance.get('InstanceType', 't3.medium')
        lifecycle = instance.get('InstanceLifecycle', 'on-demand')
        
        # Get base price
        base_price = EC2_PRICING.get(instance_type, 0.0464)
        
        # Apply spot discount
        if lifecycle == 'spot':
            return base_price * SPOT_DISCOUNT
        
        return base_price
    
    def calculate_spot_savings(self, instances: List[Dict]) -> Tuple[float, float]:
        """Calculate savings from using spot instances"""
        total_spot_cost = 0.0
        total_ondemand_equivalent = 0.0
        
        for instance in instances:
            instance_type = instance.get('InstanceType', 't3.medium')
            lifecycle = instance.get('InstanceLifecycle', 'on-demand')
            base_price = EC2_PRICING.get(instance_type, 0.0464)
            
            if lifecycle == 'spot':
                total_spot_cost += base_price * SPOT_DISCOUNT
                total_ondemand_equivalent += base_price
            else:
                total_spot_cost += base_price
                total_ondemand_equivalent += base_price
        
        savings = total_ondemand_equivalent - total_spot_cost
        percentage = (savings / total_ondemand_equivalent * 100) if total_ondemand_equivalent > 0 else 0
        
        return savings, percentage
    
    def calculate_resource_costs(self, instances: List[Dict]) -> Tuple[float, float]:
        """Calculate cost per CPU and memory"""
        total_cost = sum(self.calculate_instance_cost(i) for i in instances)
        total_cpus = 0
        total_memory_gb = 0
        
        for instance in instances:
            instance_type = instance.get('InstanceType', 't3.medium')
            specs = INSTANCE_SPECS.get(instance_type, (2, 4))
            total_cpus += specs[0]
            total_memory_gb += specs[1]
        
        cost_per_cpu = total_cost / total_cpus if total_cpus > 0 else 0
        cost_per_gb = total_cost / total_memory_gb if total_memory_gb > 0 else 0
        
        return cost_per_cpu, cost_per_gb
    
    def detect_optimization_opportunities(self, instances: List[Dict]) -> Dict[str, float]:
        """Detect potential cost optimization opportunities"""
        opportunities = {
            'underutilized_instances': 0.0,
            'non_spot_eligible': 0.0,
            'oversized_instances': 0.0
        }
        
        # Estimate savings from converting on-demand to spot (simple heuristic)
        ondemand_count = sum(1 for i in instances if i.get('InstanceLifecycle', 'on-demand') == 'on-demand')
        if ondemand_count > 0:
            # Assume we can convert 30% more to spot
            convertible = int(ondemand_count * 0.3)
            avg_cost = sum(EC2_PRICING.get(i.get('InstanceType', 't3.medium'), 0.0464) for i in instances if i.get('InstanceLifecycle', 'on-demand') == 'on-demand') / ondemand_count if ondemand_count > 0 else 0
            opportunities['non_spot_eligible'] = convertible * avg_cost * (1 - SPOT_DISCOUNT)
        
        return opportunities
    
    def update_metrics(self):
        """Update all Prometheus metrics with comprehensive cost data"""
        instances = self.get_running_instances()

        if not instances:
            print("No running instances found")
            return

        # Calculate total costs
        total_hourly = sum(self.calculate_instance_cost(i) for i in instances)
        total_daily = total_hourly * 24
        total_monthly = total_daily * 30

        # Update aggregate metrics
        total_cluster_cost_hourly.labels(cluster=self.cluster_name).set(total_hourly)
        total_cluster_cost_daily.labels(cluster=self.cluster_name).set(total_daily)
        total_cluster_cost_monthly.labels(cluster=self.cluster_name).set(total_monthly)

        # Track instance costs by various dimensions
        cost_by_lifecycle_data = {'spot': 0.0, 'on-demand': 0.0}
        cost_by_type_data = {}
        cost_by_az_data = {}

        # Remove stale gauge labels for terminated instances
        current_ids = {i['InstanceId'] for i in instances}
        if hasattr(self, '_previous_instance_labels'):
            for label_set in self._previous_instance_labels:
                if label_set['instance_id'] not in current_ids:
                    try:
                        ec2_cost_per_hour.remove(
                            label_set['instance_id'], label_set['instance_type'],
                            label_set['lifecycle'], label_set['availability_zone'],
                            self.cluster_name
                        )
                    except Exception:
                        pass

        current_labels = []
        for instance in instances:
            instance_id = instance['InstanceId']
            instance_type = instance.get('InstanceType', 'unknown')
            lifecycle = instance.get('InstanceLifecycle', 'on-demand')
            az = instance['Placement'].get('AvailabilityZone', 'unknown')

            cost = self.calculate_instance_cost(instance)

            # Per-instance metric
            ec2_cost_per_hour.labels(
                instance_id=instance_id,
                instance_type=instance_type,
                lifecycle=lifecycle,
                availability_zone=az,
                cluster=self.cluster_name
            ).set(cost)
            current_labels.append({'instance_id': instance_id, 'instance_type': instance_type,
                                    'lifecycle': lifecycle, 'availability_zone': az})

            # Aggregate by lifecycle
            cost_by_lifecycle_data[lifecycle] = cost_by_lifecycle_data.get(lifecycle, 0.0) + cost

            # Aggregate by type
            cost_by_type_data[instance_type] = cost_by_type_data.get(instance_type, 0.0) + cost

            # Aggregate by AZ
            cost_by_az_data[az] = cost_by_az_data.get(az, 0.0) + cost

        self._previous_instance_labels = current_labels

        # Update breakdown metrics
        for lifecycle, cost in cost_by_lifecycle_data.items():
            cost_by_lifecycle.labels(lifecycle=lifecycle, cluster=self.cluster_name).set(cost)
        
        for instance_type, cost in cost_by_type_data.items():
            cost_by_instance_type.labels(instance_type=instance_type, cluster=self.cluster_name).set(cost)
        
        for az, cost in cost_by_az_data.items():
            cost_by_az.labels(availability_zone=az, cluster=self.cluster_name).set(cost)
        
        # Calculate and update savings metrics
        savings, percentage = self.calculate_spot_savings(instances)
        spot_savings_hourly.labels(cluster=self.cluster_name).set(savings)
        spot_savings_percentage.labels(cluster=self.cluster_name).set(percentage)

        # Autoscaling savings vs static fleet: 1×t3.medium master + 5×t3.small workers all on-demand
        STATIC_FLEET_COST = EC2_PRICING['t3.medium'] + 5 * EC2_PRICING['t3.small']  # $0.1624/hr
        autoscaling_savings_pct = max(0.0, (STATIC_FLEET_COST - total_hourly) / STATIC_FLEET_COST * 100)
        autoscaling_savings_percentage.labels(cluster=self.cluster_name).set(autoscaling_savings_pct)

        # Calculate efficiency metrics
        pod_count = self.get_pod_count()
        cost_per_pod.labels(cluster=self.cluster_name).set(total_hourly / pod_count if pod_count > 0 else 0)
        
        cost_per_cpu, cost_per_gb = self.calculate_resource_costs(instances)
        cost_per_cpu_core.labels(cluster=self.cluster_name).set(cost_per_cpu)
        cost_per_gb_memory.labels(cluster=self.cluster_name).set(cost_per_gb)
        
        # Budget tracking
        days_in_month = 30
        current_day = datetime.now().day
        projected_monthly = total_hourly * 24 * days_in_month
        actual_so_far = total_hourly * 24 * current_day
        budget_used = (actual_so_far / self.monthly_budget) * 100
        budget_remaining = self.monthly_budget - actual_so_far
        
        budget_usage_percentage.labels(cluster=self.cluster_name).set(budget_used)
        budget_remaining_dollars.labels(cluster=self.cluster_name).set(max(0, budget_remaining))
        
        # Optimization opportunities
        opportunities = self.detect_optimization_opportunities(instances)
        for opt_type, savings in opportunities.items():
            potential_savings_hourly.labels(
                optimization_type=opt_type,
                cluster=self.cluster_name
            ).set(savings)
        
        # Cost trend
        if self.previous_cost > 0:
            time_diff = (datetime.now() - self.previous_timestamp).total_seconds() / 3600  # hours
            cost_change = total_hourly - self.previous_cost
            rate = cost_change / time_diff if time_diff > 0 else 0
            cost_increase_rate.labels(cluster=self.cluster_name).set(rate)
        
        self.previous_cost = total_hourly
        self.previous_timestamp = datetime.now()
        
        print(f"\n=== Cost Metrics Updated ===")
        print(f"Instances: {len(instances)}")
        print(f"Hourly: ${total_hourly:.4f} | Daily: ${total_daily:.2f} | Monthly: ${total_monthly:.2f}")
        print(f"Spot Savings: ${savings:.4f}/hr ({percentage:.1f}%)")
        print(f"Budget: {budget_used:.1f}% used, ${budget_remaining:.2f} remaining")
        cost_per_pod_val = total_hourly / pod_count if pod_count > 0 else 0
        print(f"Cost/Pod: ${cost_per_pod_val:.6f} | Cost/CPU: ${cost_per_cpu:.4f} | Cost/GB: ${cost_per_gb:.4f}")
        print("="*30)
    
    def run(self, interval: int = 30):
        """Run exporter with periodic updates"""
        print(f"Starting Enhanced Cost Exporter for cluster '{self.cluster_name}'")
        print(f"Region: {self.region} | Monthly Budget: ${self.monthly_budget}")
        print(f"Metrics update interval: {interval}s")
        print(f"Metrics endpoint: http://localhost:9100/metrics\n")
        
        while True:
            try:
                self.update_metrics()
            except Exception as e:
                print(f"Error updating metrics: {e}")
                import traceback
                traceback.print_exc()
            
            time.sleep(interval)


if __name__ == '__main__':
    # Start Prometheus HTTP server on port 9100
    port = int(os.getenv('METRICS_PORT', 9100))
    start_http_server(port)
    print(f"✓ Prometheus metrics server started on port {port}")
    
    # Configuration from environment
    cluster_name = os.getenv('CLUSTER_NAME', 'node-fleet')
    region = os.getenv('AWS_REGION', 'ap-southeast-1')
    monthly_budget = float(os.getenv('MONTHLY_BUDGET', '500'))
    update_interval = int(os.getenv('UPDATE_INTERVAL', '30'))
    
    # Run enhanced exporter
    exporter = EnhancedCostExporter(
        cluster_name=cluster_name,
        region=region,
        monthly_budget=monthly_budget
    )
    exporter.run(interval=update_interval)
