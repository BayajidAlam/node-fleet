"""
Prometheus metrics collector
Queries Prometheus HTTP API for cluster metrics
"""

import logging
import requests
import time
from typing import Dict, Optional
from prometheus_api_client import PrometheusConnect

logger = logging.getLogger()

# Metric cache to reduce Prometheus load
_metrics_cache = {}
_cache_ttl_seconds = 30  # Cache metrics for 30 seconds

# PromQL queries
QUERIES = {
    "cpu_usage": 'avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100',
    "memory_usage": '(1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100',
    "pending_pods": 'sum(kube_pod_status_phase{phase="Pending"})',
    "node_count": 'count(kube_node_info)',
    "network_receive_mbps": 'sum(rate(node_network_receive_bytes_total{device!~"lo|veth.*"}[5m])) / 1024 / 1024',
    "network_transmit_mbps": 'sum(rate(node_network_transmit_bytes_total{device!~"lo|veth.*"}[5m])) / 1024 / 1024',
    "disk_read_mbps": 'sum(rate(node_disk_read_bytes_total[5m])) / 1024 / 1024',
    "disk_write_mbps": 'sum(rate(node_disk_written_bytes_total[5m])) / 1024 / 1024',
}


def collect_metrics(prometheus_url: str, username: str = None, password: str = None, use_cache: bool = True) -> Dict[str, float]:
    """
    Query Prometheus for current cluster metrics with caching support
    
    Args:
        prometheus_url: Prometheus endpoint URL (e.g., http://master-ip:30090)
        username: Basic auth username (optional)
        password: Basic auth password (optional)
        use_cache: Whether to use cached metrics (default: True)
    
    Returns:
        Dictionary with cpu_usage, memory_usage, pending_pods, node_count, 
        network_receive_mbps, network_transmit_mbps, disk_read_mbps, disk_write_mbps
    """
    if not prometheus_url:
        logger.error("Prometheus URL not configured")
        raise ValueError("PROMETHEUS_URL environment variable not set")
    
    # Check cache first
    if use_cache:
        cached_metrics = get_cached_metrics()
        if cached_metrics:
            logger.info("Using cached metrics (age: {:.1f}s)".format(
                time.time() - _metrics_cache.get('timestamp', 0)
            ))
            return cached_metrics
    
    try:
        # Configure Prometheus connection with basic auth
        headers = {}
        if username and password:
            import base64
            credentials = f"{username}:{password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers = {"Authorization": f"Basic {encoded}"}
            logger.info("Using basic authentication for Prometheus")
        
        prom = PrometheusConnect(url=prometheus_url, headers=headers, disable_ssl=True)
        metrics = {}
        
        for metric_name, query in QUERIES.items():
            try:
                result = prom.custom_query(query=query)
                
                if result and len(result) > 0:
                    value = float(result[0]["value"][1])
                    metrics[metric_name] = value
                else:
                    # Default to 0 if no data
                    metrics[metric_name] = 0.0
                    logger.warning(f"No data returned for {metric_name}")
                    
            except Exception as e:
                logger.error(f"Error querying {metric_name}: {str(e)}")
                metrics[metric_name] = 0.0
        
        logger.info(f"Successfully collected metrics: {metrics}")
        
        # Cache metrics for future use
        if use_cache:
            cache_metrics(metrics)
        
        return metrics
        
    except Exception as e:
        logger.error(f"Failed to collect metrics from Prometheus: {str(e)}")
        
        # Try to return cached metrics if available
        if use_cache:
            cached = get_cached_metrics(ignore_ttl=True)
            if cached:
                logger.warning("Prometheus unreachable, using stale cached metrics")
                return cached
        
        raise


def get_cached_metrics(ignore_ttl: bool = False) -> Optional[Dict[str, float]]:
    """
    Get cached metrics if available and fresh
    
    Args:
        ignore_ttl: Return cache even if expired (for fallback)
    
    Returns:
        Cached metrics or None
    """
    if 'metrics' not in _metrics_cache:
        return None
    
    cache_age = time.time() - _metrics_cache.get('timestamp', 0)
    
    if not ignore_ttl and cache_age > _cache_ttl_seconds:
        return None
    
    return _metrics_cache['metrics']


def cache_metrics(metrics: Dict[str, float]) -> None:
    """
    Cache metrics with timestamp
    
    Args:
        metrics: Metrics to cache
    """
    global _metrics_cache
    _metrics_cache = {
        'metrics': metrics,
        'timestamp': time.time()
    }
    logger.debug(f"Cached metrics: {list(metrics.keys())}")
