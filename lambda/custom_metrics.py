"""
Custom application metrics collector for app-specific scaling decisions.
Supports queue depth, API latency, request rate, and custom business metrics.
"""

import logging
from typing import Dict, List, Optional
from prometheus_api_client import PrometheusConnect

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class CustomMetricsCollector:
    """Collects and evaluates custom application metrics for scaling decisions"""
    
    def __init__(self, prometheus_url: str):
        """
        Initialize custom metrics collector
        
        Args:
            prometheus_url: Prometheus server URL
        """
        self.prom = PrometheusConnect(url=prometheus_url, disable_ssl=True)
    
    def get_queue_depth(self, queue_name: str = "default") -> Optional[int]:
        """
        Get current queue depth from application metrics
        
        Args:
            queue_name: Queue name to monitor
        
        Returns:
            Current queue depth or None if metric unavailable
        """
        try:
            query = f'app_queue_depth{{queue="{queue_name}"}}'
            result = self.prom.custom_query(query=query)
            
            if result and len(result) > 0:
                depth = int(float(result[0]['value'][1]))
                logger.info(f"Queue '{queue_name}' depth: {depth}")
                return depth
            
            logger.warning(f"No queue depth metric found for queue '{queue_name}'")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching queue depth: {e}")
            return None
    
    def get_api_latency_p95(self, service: str = "api") -> Optional[float]:
        """
        Get 95th percentile API latency
        
        Args:
            service: Service name to monitor
        
        Returns:
            P95 latency in milliseconds or None
        """
        try:
            # Query for 95th percentile over last 5 minutes
            query = f'histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{{service="{service}"}}[5m])) * 1000'
            result = self.prom.custom_query(query=query)
            
            if result and len(result) > 0:
                latency_ms = float(result[0]['value'][1])
                logger.info(f"API P95 latency for '{service}': {latency_ms:.2f}ms")
                return latency_ms
            
            logger.warning(f"No latency metric found for service '{service}'")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching API latency: {e}")
            return None
    
    def get_request_rate(self, service: str = "api", window: str = "5m") -> Optional[float]:
        """
        Get requests per second for a service
        
        Args:
            service: Service name to monitor
            window: Time window for rate calculation
        
        Returns:
            Requests per second or None
        """
        try:
            query = f'rate(http_requests_total{{service="{service}"}}[{window}])'
            result = self.prom.custom_query(query=query)
            
            if result and len(result) > 0:
                rps = float(result[0]['value'][1])
                logger.info(f"Request rate for '{service}': {rps:.2f} req/s")
                return rps
            
            logger.warning(f"No request rate metric found for service '{service}'")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching request rate: {e}")
            return None
    
    def get_error_rate(self, service: str = "api", window: str = "5m") -> Optional[float]:
        """
        Get error rate percentage for a service
        
        Args:
            service: Service name to monitor
            window: Time window for rate calculation
        
        Returns:
            Error rate percentage (0-100) or None
        """
        try:
            # Calculate error rate: errors / total requests
            query = f'(rate(http_requests_total{{service="{service}", status=~"5.."}}[{window}]) / rate(http_requests_total{{service="{service}"}}[{window}])) * 100'
            result = self.prom.custom_query(query=query)
            
            if result and len(result) > 0:
                error_rate = float(result[0]['value'][1])
                logger.info(f"Error rate for '{service}': {error_rate:.2f}%")
                return error_rate
            
            return 0.0  # No errors found
            
        except Exception as e:
            logger.error(f"Error fetching error rate: {e}")
            return None
    
    def get_active_connections(self, service: str = "api") -> Optional[int]:
        """
        Get current active connections/sessions
        
        Args:
            service: Service name to monitor
        
        Returns:
            Number of active connections or None
        """
        try:
            query = f'app_active_connections{{service="{service}"}}'
            result = self.prom.custom_query(query=query)
            
            if result and len(result) > 0:
                connections = int(float(result[0]['value'][1]))
                logger.info(f"Active connections for '{service}': {connections}")
                return connections
            
            logger.warning(f"No active connections metric found for service '{service}'")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching active connections: {e}")
            return None
    
    def get_custom_metric(self, metric_name: str, labels: Dict[str, str] = None) -> Optional[float]:
        """
        Get any custom metric by name and labels
        
        Args:
            metric_name: Prometheus metric name
            labels: Optional label filters
        
        Returns:
            Metric value or None
        """
        try:
            # Build label selector
            label_str = ""
            if labels:
                label_parts = [f'{k}="{v}"' for k, v in labels.items()]
                label_str = "{" + ",".join(label_parts) + "}"
            
            query = f'{metric_name}{label_str}'
            result = self.prom.custom_query(query=query)
            
            if result and len(result) > 0:
                value = float(result[0]['value'][1])
                logger.info(f"Custom metric '{metric_name}': {value}")
                return value
            
            logger.warning(f"No value found for custom metric '{metric_name}'")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching custom metric '{metric_name}': {e}")
            return None
    
    def evaluate_scaling_need(self, thresholds: Dict[str, any]) -> Dict[str, any]:
        """
        Evaluate if scaling is needed based on custom metrics
        
        Args:
            thresholds: Dict with threshold configurations:
                - queue_depth_max: Max queue depth before scale-up
                - latency_p95_max_ms: Max acceptable P95 latency
                - error_rate_max_percent: Max error rate percentage
                - connections_per_node: Target connections per node
        
        Returns:
            Dict with scale_needed (bool) and reasons (list)
        """
        reasons = []
        scale_up = False
        
        # Check queue depth
        queue_threshold = thresholds.get('queue_depth_max', 100)
        queue_depth = self.get_queue_depth()
        if queue_depth and queue_depth > queue_threshold:
            scale_up = True
            reasons.append(f"Queue depth ({queue_depth}) exceeds threshold ({queue_threshold})")
        
        # Check API latency
        latency_threshold = thresholds.get('latency_p95_max_ms', 500)
        latency = self.get_api_latency_p95()
        if latency and latency > latency_threshold:
            scale_up = True
            reasons.append(f"P95 latency ({latency:.0f}ms) exceeds threshold ({latency_threshold}ms)")
        
        # Check error rate
        error_threshold = thresholds.get('error_rate_max_percent', 5.0)
        error_rate = self.get_error_rate()
        if error_rate and error_rate > error_threshold:
            scale_up = True
            reasons.append(f"Error rate ({error_rate:.1f}%) exceeds threshold ({error_threshold}%)")
        
        # Check connections per node
        connections_per_node_target = thresholds.get('connections_per_node', 1000)
        active_connections = self.get_active_connections()
        if active_connections:
            # This would need current node count - placeholder logic
            estimated_nodes_needed = (active_connections / connections_per_node_target) + 1
            if estimated_nodes_needed > 1:  # Simplified check
                scale_up = True
                reasons.append(f"Active connections ({active_connections}) require more capacity")
        
        result = {
            'scale_needed': scale_up,
            'reasons': reasons,
            'metrics': {
                'queue_depth': queue_depth,
                'latency_p95_ms': latency,
                'error_rate_percent': error_rate,
                'active_connections': active_connections
            }
        }
        
        logger.info(f"Custom metrics evaluation: {result}")
        return result


def get_custom_metrics(prometheus_url: str, thresholds: Dict = None) -> Dict:
    """
    Convenience function to collect all custom metrics
    
    Args:
        prometheus_url: Prometheus server URL
        thresholds: Optional thresholds for evaluation
    
    Returns:
        Dict with custom metrics and scaling recommendation
    """
    collector = CustomMetricsCollector(prometheus_url)
    
    default_thresholds = {
        'queue_depth_max': 100,
        'latency_p95_max_ms': 500,
        'error_rate_max_percent': 5.0,
        'connections_per_node': 1000
    }
    
    thresholds = thresholds or default_thresholds
    
    return collector.evaluate_scaling_need(thresholds)
