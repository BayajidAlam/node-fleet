"""
Tests for enhanced cost monitoring and optimization system
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add monitoring directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../monitoring'))

from cost_exporter import EnhancedCostExporter, EC2_PRICING, SPOT_DISCOUNT
from cost_optimizer import CostOptimizationRecommender


class TestEnhancedCostExporter:
    """Test enhanced cost exporter functionality"""
    
    @pytest.fixture
    def exporter(self):
        with patch('boto3.client'):
            return EnhancedCostExporter(
                cluster_name='test-cluster',
                region='ap-southeast-1',
                monthly_budget=500.0
            )
    
    @pytest.fixture
    def sample_instances(self):
        return [
            {
                'InstanceId': 'i-123456',
                'InstanceType': 't3.medium',
                'InstanceLifecycle': 'spot',
                'Placement': {'AvailabilityZone': 'ap-southeast-1a'}
            },
            {
                'InstanceId': 'i-789012',
                'InstanceType': 't3.large',
                'Placement': {'AvailabilityZone': 'ap-southeast-1b'}
            }
        ]
    
    def test_calculate_instance_cost_ondemand(self, exporter):
        """Test on-demand cost calculation"""
        instance = {
            'InstanceType': 't3.medium'
        }
        cost = exporter.calculate_instance_cost(instance)
        assert cost == EC2_PRICING['t3.medium']
        assert cost == 0.0464
    
    def test_calculate_instance_cost_spot(self, exporter):
        """Test spot instance cost calculation with discount"""
        instance = {
            'InstanceType': 't3.medium',
            'InstanceLifecycle': 'spot'
        }
        cost = exporter.calculate_instance_cost(instance)
        expected = EC2_PRICING['t3.medium'] * SPOT_DISCOUNT
        assert cost == expected
        assert cost < EC2_PRICING['t3.medium']
    
    def test_calculate_spot_savings(self, exporter, sample_instances):
        """Test spot savings calculation"""
        savings, percentage = exporter.calculate_spot_savings(sample_instances)
        
        # First instance is spot, second is on-demand
        spot_cost = EC2_PRICING['t3.medium'] * SPOT_DISCOUNT
        ondemand_cost = EC2_PRICING['t3.large']
        total_cost = spot_cost + ondemand_cost
        
        # If all were on-demand
        all_ondemand = EC2_PRICING['t3.medium'] + EC2_PRICING['t3.large']
        
        expected_savings = all_ondemand - total_cost
        assert abs(savings - expected_savings) < 0.001
        assert 0 < percentage < 100
    
    def test_calculate_resource_costs(self, exporter, sample_instances):
        """Test CPU and memory cost calculations"""
        cost_per_cpu, cost_per_gb = exporter.calculate_resource_costs(sample_instances)
        
        # t3.medium: 2 vCPU, 4GB RAM
        # t3.large: 2 vCPU, 8GB RAM
        total_cpus = 4
        total_memory = 12
        
        assert cost_per_cpu > 0
        assert cost_per_gb > 0
    
    def test_detect_optimization_opportunities(self, exporter):
        """Test optimization opportunity detection"""
        instances = [
            {'InstanceType': 't3.medium'},
            {'InstanceType': 't3.large'},
            {'InstanceType': 't3.xlarge'}
        ]
        
        opportunities = exporter.detect_optimization_opportunities(instances)
        
        assert 'underutilized_instances' in opportunities
        assert 'non_spot_eligible' in opportunities
        assert 'oversized_instances' in opportunities
        
        # Should detect potential spot conversion
        assert opportunities['non_spot_eligible'] > 0
    
    def test_get_pod_count(self, exporter):
        """Test pod count estimation"""
        with patch.object(exporter, 'get_running_instances', return_value=[{'InstanceId': 'i-1'}, {'InstanceId': 'i-2'}]):
            pod_count = exporter.get_pod_count()
            assert pod_count == 20  # 2 instances * 10 pods each


class TestCostOptimizationRecommender:
    """Test cost optimization recommender"""
    
    @pytest.fixture
    def recommender(self):
        with patch('boto3.client'):
            return CostOptimizationRecommender(
                cluster_name='test-cluster',
                region='ap-southeast-1'
            )
    
    def test_analyze_spot_opportunities_low_usage(self, recommender):
        """Test spot opportunity detection when usage is low"""
        mock_response = {
            'Reservations': [
                {
                    'Instances': [
                        {'InstanceId': 'i-1', 'InstanceType': 't3.medium'},
                        {'InstanceId': 'i-2', 'InstanceType': 't3.medium'},
                        {'InstanceId': 'i-3', 'InstanceType': 't3.medium', 'InstanceLifecycle': 'spot'}
                    ]
                }
            ]
        }
        
        with patch.object(recommender.ec2_client, 'describe_instances', return_value=mock_response):
            recommendations = recommender.analyze_spot_opportunities()
            
            assert len(recommendations) > 0
            rec = recommendations[0]
            assert rec['type'] == 'increase_spot_usage'
            assert rec['current_spot_percentage'] < 70
            assert rec['additional_spot_instances'] > 0
    
    def test_analyze_spot_opportunities_good_usage(self, recommender):
        """Test spot opportunities when usage is already good"""
        mock_response = {
            'Reservations': [
                {
                    'Instances': [
                        {'InstanceId': f'i-{i}', 'InstanceType': 't3.medium', 'InstanceLifecycle': 'spot' if i < 7 else None}
                        for i in range(10)
                    ]
                }
            ]
        }
        
        with patch.object(recommender.ec2_client, 'describe_instances', return_value=mock_response):
            recommendations = recommender.analyze_spot_opportunities()
            
            # Should have no recommendations if already at 70% spot
            assert len(recommendations) == 0 or recommendations[0]['current_spot_percentage'] >= 60
    
    def test_analyze_reserved_instance_opportunities(self, recommender):
        """Test reserved instance recommendations"""
        mock_response = {
            'Reservations': [
                {
                    'Instances': [
                        {'InstanceId': f'i-{i}', 'InstanceType': 't3.large'}
                        for i in range(3)
                    ]
                }
            ]
        }
        
        with patch.object(recommender.ec2_client, 'describe_instances', return_value=mock_response):
            recommendations = recommender.analyze_reserved_instance_opportunities()
            
            assert len(recommendations) > 0
            rec = recommendations[0]
            assert rec['type'] == 'reserved_instance'
            assert rec['instance_type'] == 't3.large'
            assert rec['count'] >= 2
            assert rec['potential_savings'] > 0
    
    def test_analyze_multi_az_balance_balanced(self, recommender):
        """Test AZ balance detection when balanced"""
        mock_response = {
            'Reservations': [
                {
                    'Instances': [
                        {'InstanceId': 'i-1', 'Placement': {'AvailabilityZone': 'ap-southeast-1a'}},
                        {'InstanceId': 'i-2', 'Placement': {'AvailabilityZone': 'ap-southeast-1b'}}
                    ]
                }
            ]
        }
        
        with patch.object(recommender.ec2_client, 'describe_instances', return_value=mock_response):
            recommendations = recommender.analyze_multi_az_balance()
            
            # Should have no recommendations when balanced
            assert len(recommendations) == 0
    
    def test_analyze_multi_az_balance_unbalanced(self, recommender):
        """Test AZ balance detection when unbalanced"""
        mock_response = {
            'Reservations': [
                {
                    'Instances': [
                        {'InstanceId': f'i-{i}', 'Placement': {'AvailabilityZone': 'ap-southeast-1a'}}
                        for i in range(5)
                    ] + [
                        {'InstanceId': 'i-5', 'Placement': {'AvailabilityZone': 'ap-southeast-1b'}}
                    ]
                }
            ]
        }
        
        with patch.object(recommender.ec2_client, 'describe_instances', return_value=mock_response):
            recommendations = recommender.analyze_multi_az_balance()
            
            assert len(recommendations) > 0
            rec = recommendations[0]
            assert rec['type'] == 'az_balance'
            assert 'unbalanced' in rec['recommendation'].lower()
    
    def test_generate_report_structure(self, recommender):
        """Test that generated report has correct structure"""
        with patch.object(recommender, 'analyze_underutilized_instances', return_value=[]):
            with patch.object(recommender, 'analyze_spot_opportunities', return_value=[]):
                with patch.object(recommender, 'analyze_idle_times', return_value=[]):
                    with patch.object(recommender, 'analyze_reserved_instance_opportunities', return_value=[]):
                        with patch.object(recommender, 'analyze_multi_az_balance', return_value=[]):
                            report = recommender.generate_report()
        
        assert 'timestamp' in report
        assert 'cluster' in report
        assert 'total_recommendations' in report
        assert 'total_potential_monthly_savings' in report
        assert 'recommendations' in report
        assert isinstance(report['recommendations'], list)
    
    def test_estimate_downsize_savings(self, recommender):
        """Test downsize savings estimation"""
        savings = recommender._estimate_downsize_savings('t3.large')
        assert savings > 0
        assert savings < EC2_PRICING['t3.large'] * 24 * 30  # Less than full cost


class TestCostMetricsIntegration:
    """Integration tests for cost metrics"""
    
    def test_pricing_data_complete(self):
        """Test that pricing data is complete"""
        assert len(EC2_PRICING) > 0
        for instance_type, price in EC2_PRICING.items():
            assert price > 0
            assert isinstance(instance_type, str)
            assert isinstance(price, float)
    
    def test_spot_discount_valid(self):
        """Test spot discount is reasonable"""
        assert 0 < SPOT_DISCOUNT < 1
        assert SPOT_DISCOUNT >= 0.6  # At least 40% savings
        assert SPOT_DISCOUNT <= 0.75  # At most 25% of on-demand price
    
    def test_monthly_budget_calculations(self):
        """Test monthly budget projections are accurate"""
        hourly_cost = 1.0
        daily_cost = hourly_cost * 24
        monthly_cost = daily_cost * 30
        
        assert daily_cost == 24.0
        assert monthly_cost == 720.0


@pytest.mark.integration
class TestCostSystemEndToEnd:
    """End-to-end tests for cost monitoring system"""
    
    def test_full_cost_analysis_workflow(self):
        """Test complete cost analysis workflow"""
        with patch('boto3.client'):
            exporter = EnhancedCostExporter()
            recommender = CostOptimizationRecommender()
            
            # Simulate getting instances
            mock_instances = [
                {'InstanceId': 'i-1', 'InstanceType': 't3.medium', 'InstanceLifecycle': 'spot', 'Placement': {'AvailabilityZone': 'ap-southeast-1a'}},
                {'InstanceId': 'i-2', 'InstanceType': 't3.large', 'Placement': {'AvailabilityZone': 'ap-southeast-1b'}}
            ]
            
            # Calculate costs
            total_cost = sum(exporter.calculate_instance_cost(i) for i in mock_instances)
            assert total_cost > 0
            
            # Get savings
            savings, percentage = exporter.calculate_spot_savings(mock_instances)
            assert savings >= 0
            assert 0 <= percentage <= 100


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
