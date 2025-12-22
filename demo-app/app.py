"""
Demo Flask application with Prometheus custom metrics instrumentation.
Exposes queue depth, latency, and connection metrics for autoscaling.
"""

from flask import Flask, request, jsonify
import time
import random
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from prometheus_client import CONTENT_TYPE_LATEST
import logging

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Prometheus metrics
http_requests_total = Counter(
    'http_requests_total',
    'Total HTTP requests',
    ['service', 'method', 'endpoint', 'status']
)

http_request_duration_seconds = Histogram(
    'http_request_duration_seconds',
    'HTTP request latency',
    ['service', 'method', 'endpoint']
)

app_queue_depth = Gauge(
    'app_queue_depth',
    'Current queue depth',
    ['queue']
)

app_active_connections = Gauge(
    'app_active_connections',
    'Number of active connections',
    ['service']
)

# Simulated queue and connection state
simulated_queue = []
active_connections_count = 0


@app.before_request
def before_request():
    """Track request start time and increment connections"""
    global active_connections_count
    active_connections_count += 1
    app_active_connections.labels(service='api').set(active_connections_count)
    request.start_time = time.time()


@app.after_request
def after_request(response):
    """Record metrics after request"""
    global active_connections_count
    active_connections_count = max(0, active_connections_count - 1)
    app_active_connections.labels(service='api').set(active_connections_count)
    
    # Record request duration
    if hasattr(request, 'start_time'):
        duration = time.time() - request.start_time
        http_request_duration_seconds.labels(
            service='api',
            method=request.method,
            endpoint=request.endpoint or 'unknown'
        ).observe(duration)
    
    # Record request count
    http_requests_total.labels(
        service='api',
        method=request.method,
        endpoint=request.endpoint or 'unknown',
        status=response.status_code
    ).inc()
    
    return response


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': time.time()})


@app.route('/api/process', methods=['POST'])
def process_task():
    """
    Simulate task processing with variable latency.
    Adds tasks to queue and processes them.
    """
    global simulated_queue
    
    data = request.get_json() or {}
    task_id = data.get('task_id', random.randint(1000, 9999))
    complexity = data.get('complexity', 'normal')
    
    # Add to queue
    simulated_queue.append({'task_id': task_id, 'complexity': complexity})
    app_queue_depth.labels(queue='default').set(len(simulated_queue))
    
    # Simulate processing time based on complexity
    if complexity == 'simple':
        time.sleep(random.uniform(0.01, 0.05))
    elif complexity == 'complex':
        time.sleep(random.uniform(0.1, 0.3))
    else:  # normal
        time.sleep(random.uniform(0.05, 0.15))
    
    # Remove from queue
    if simulated_queue:
        processed = simulated_queue.pop(0)
        app_queue_depth.labels(queue='default').set(len(simulated_queue))
        
        return jsonify({
            'status': 'processed',
            'task_id': processed['task_id'],
            'queue_depth': len(simulated_queue)
        })
    
    return jsonify({'status': 'error', 'message': 'Queue empty'}), 500


@app.route('/api/data')
def get_data():
    """Simple data retrieval endpoint with low latency"""
    # Simulate database query
    time.sleep(random.uniform(0.001, 0.01))
    
    return jsonify({
        'data': [
            {'id': i, 'value': random.randint(1, 100)}
            for i in range(10)
        ],
        'count': 10
    })


@app.route('/api/heavy')
def heavy_computation():
    """
    Simulate heavy computation endpoint.
    Useful for load testing and triggering autoscaling.
    """
    # Simulate CPU-intensive work
    duration = random.uniform(0.2, 0.5)
    time.sleep(duration)
    
    # Randomly introduce errors (5% rate)
    if random.random() < 0.05:
        return jsonify({'error': 'Processing failed'}), 500
    
    return jsonify({
        'status': 'completed',
        'computation_time': duration,
        'result': random.randint(1000, 9999)
    })


@app.route('/api/queue/add', methods=['POST'])
def add_to_queue():
    """Add multiple items to queue without processing"""
    global simulated_queue
    
    data = request.get_json() or {}
    count = data.get('count', 1)
    
    for i in range(count):
        simulated_queue.append({
            'task_id': random.randint(1000, 9999),
            'complexity': 'normal'
        })
    
    app_queue_depth.labels(queue='default').set(len(simulated_queue))
    
    return jsonify({
        'status': 'added',
        'count': count,
        'queue_depth': len(simulated_queue)
    })


@app.route('/api/queue/clear', methods=['POST'])
def clear_queue():
    """Clear the queue"""
    global simulated_queue
    
    cleared_count = len(simulated_queue)
    simulated_queue = []
    app_queue_depth.labels(queue='default').set(0)
    
    return jsonify({
        'status': 'cleared',
        'cleared_count': cleared_count,
        'queue_depth': 0
    })


@app.route('/metrics')
def metrics():
    """Prometheus metrics endpoint"""
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}


@app.route('/')
def index():
    """Index page with API documentation"""
    return jsonify({
        'service': 'demo-api',
        'version': '1.0.0',
        'endpoints': {
            '/health': 'Health check',
            '/api/process': 'Process task (POST)',
            '/api/data': 'Get data',
            '/api/heavy': 'Heavy computation',
            '/api/queue/add': 'Add to queue (POST)',
            '/api/queue/clear': 'Clear queue (POST)',
            '/metrics': 'Prometheus metrics'
        },
        'metrics': {
            'queue_depth': len(simulated_queue),
            'active_connections': active_connections_count
        }
    })


if __name__ == '__main__':
    # Initialize queue depth metric
    app_queue_depth.labels(queue='default').set(0)
    app_active_connections.labels(service='api').set(0)
    
    logger.info("Starting demo API server with custom metrics...")
    logger.info("Metrics available at http://localhost:5000/metrics")
    
    # Run on all interfaces for Docker/K8s compatibility
    app.run(host='0.0.0.0', port=5000, debug=False)
