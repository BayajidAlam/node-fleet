"""
Prometheus metrics collector
Queries Prometheus HTTP API for cluster metrics
"""

import logging
import requests
from typing import Dict
from prometheus_api_client import PrometheusConnect

logger = logging.getLogger()

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


def collect_metrics(prometheus_url: str) -> Dict[str, float]:
    """
    Query Prometheus for current cluster metrics
    
    Args:
        prometheus_url: Prometheus endpoint URL (e.g., http://master-ip:30090)
    
    Returns:
        Dictionary with cpu_usage, memory_usage, pending_pods, node_count, 
        network_receive_mbps, network_transmit_mbps, disk_read_mbps, disk_write_mbps
    """
    if not prometheus_url:
        logger.error("Prometheus URL not configured")
        raise ValueError("PROMETHEUS_URL environment variable not set")
    
    try:
        prom = PrometheusConnect(url=prometheus_url, disable_ssl=True)
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
        return metrics
        
    except Exception as e:
        logger.error(f"Failed to connect to Prometheus at {prometheus_url}: {str(e)}")
        raise
