"""
Custom application metrics collector for app-specific scaling decisions.
Supports queue depth, API latency, request rate, and custom business metrics using requests.
"""

import logging
import requests
import base64
from typing import Dict, List, Optional

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class CustomMetricsCollector:
    """Collects and evaluates custom application metrics for scaling decisions"""
    
    def __init__(self, prometheus_url: str, username: str = None, password: str = None):
        """
        Initialize custom metrics collector
        """
        self.prometheus_url = prometheus_url
        self.headers = {}
        if username and password:
            credentials = f"{username}:{password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            self.headers = {"Authorization": f"Basic {encoded}"}
    
    def _query(self, query: str) -> Optional[float]:
        """Helper to query Prometheus API"""
        try:
            response = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={'query': query},
                headers=self.headers,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            if data['status'] == 'success' and data['data']['result']:
                return float(data['data']['result'][0]['value'][1])
            return None
        except Exception as e:
            logger.error(f"Error querying Prometheus: {e}")
            return None

    def get_queue_depth(self, queue_name: str = "default") -> Optional[int]:
        """Get current queue depth"""
        val = self._query(f'app_queue_depth{{queue="{queue_name}"}}')
        return int(val) if val is not None else None
    
    def get_api_latency_p95(self, service: str = "api") -> Optional[float]:
        """Get 95th percentile API latency in ms"""
        query = f'histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{{service="{service}"}}[5m])) * 1000'
        return self._query(query)
    
    def get_request_rate(self, service: str = "api", window: str = "5m") -> Optional[float]:
        """Get requests per second"""
        return self._query(f'rate(http_requests_total{{service="{service}"}}[{window}])')
    
    def get_error_rate(self, service: str = "api", window: str = "5m") -> Optional[float]:
        """Get error rate percentage"""
        query = f'(rate(http_requests_total{{service="{service}", status=~"5.."}}[{window}]) / rate(http_requests_total{{service="{service}"}}[{window}])) * 100'
        return self._query(query) or 0.0
    
    def get_active_connections(self, service: str = "api") -> Optional[int]:
        """Get current active connections"""
        val = self._query(f'app_active_connections{{service="{service}"}}')
        return int(val) if val is not None else None
    
    def evaluate_scaling_need(self, thresholds: Dict[str, any]) -> Dict[str, any]:
        """Evaluate if scaling is needed based on custom metrics"""
        reasons = []
        scale_up = False
        
        queue_threshold = thresholds.get('queue_depth_max', 100)
        queue_depth = self.get_queue_depth()
        if queue_depth and queue_depth > queue_threshold:
            scale_up = True
            reasons.append(f"Queue depth ({queue_depth}) exceeds threshold ({queue_threshold})")
        
        latency_threshold = thresholds.get('latency_p95_max_ms', 500)
        latency = self.get_api_latency_p95()
        if latency and latency > latency_threshold:
            scale_up = True
            reasons.append(f"P95 latency ({latency:.0f}ms) exceeds threshold ({latency_threshold}ms)")
        
        error_threshold = thresholds.get('error_rate_max_percent', 5.0)
        error_rate = self.get_error_rate()
        if error_rate and error_rate > error_threshold:
            scale_up = True
            reasons.append(f"Error rate ({error_rate:.1f}%) exceeds threshold ({error_threshold}%)")
        
        active_connections = self.get_active_connections()
        
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


def get_custom_metrics(prometheus_url: str, username: str = None, password: str = None, thresholds: Dict = None) -> Dict:
    collector = CustomMetricsCollector(prometheus_url, username, password)
    default_thresholds = {
        'queue_depth_max': 1000,
        'latency_p95_max_ms': 2000,
        'error_rate_max_percent': 5.0,
        'connections_per_node': 1000
    }
    thresholds = thresholds or default_thresholds
    return collector.evaluate_scaling_need(thresholds)
