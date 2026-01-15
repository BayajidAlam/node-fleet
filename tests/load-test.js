import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const apiLatency = new Trend('api_latency');
const requestCount = new Counter('request_count');

// Test configuration matching REQUIREMENTS.md specifications
export const options = {
  stages: [
    // Gradual ramp-up to baseline load
    { duration: '5m', target: 50 },
    
    // Flash sale spike - simulate high traffic event
    { duration: '10m', target: 200 },
    
    // Cool-down phase - traffic drops after flash sale
    { duration: '5m', target: 50 },
    
    // Scale-down test - minimal traffic
    { duration: '5m', target: 0 },
  ],
  
  // Performance thresholds from REQUIREMENTS.md
  thresholds: {
    // 95% of requests should complete within 2 seconds
    'http_req_duration': ['p(95)<2000'],
    
    // Error rate must stay below 5%
    'http_req_failed': ['rate<0.05'],
    
    // Custom metric: API latency p99 should be under 3s
    'api_latency': ['p(99)<3000'],
    
    // Custom metric: error rate tracking
    'errors': ['rate<0.05'],
  },
};

// Environment variables - set before running
const BASE_URL = __ENV.BASE_URL || 'http://localhost:30080';
const TEST_DURATION = __ENV.TEST_DURATION || '25m';

export default function () {
  // Test multiple endpoints to simulate realistic traffic
  const endpoints = [
    { path: '/', weight: 0.4 },           // 40% - homepage
    { path: '/health', weight: 0.2 },     // 20% - health checks
    { path: '/api/products', weight: 0.2 }, // 20% - product listing
    { path: '/api/stats', weight: 0.2 },  // 20% - stats endpoint
  ];
  
  // Randomly select endpoint based on weights
  const endpoint = selectWeightedEndpoint(endpoints);
  const url = `${BASE_URL}${endpoint.path}`;
  
  // Make HTTP request
  const startTime = Date.now();
  const res = http.get(url, {
    tags: { endpoint: endpoint.path },
    timeout: '10s',
  });
  const duration = Date.now() - startTime;
  
  // Record custom metrics
  apiLatency.add(duration);
  requestCount.add(1);
  errorRate.add(res.status !== 200);
  
  // Validation checks
  const success = check(res, {
    'status is 200': (r) => r.status === 200,
    'response time < 2s': (r) => r.timings.duration < 2000,
    'response has content': (r) => r.body && r.body.length > 0,
  });
  
  if (!success) {
    console.error(`Request failed: ${endpoint.path} - Status: ${res.status}`);
  }
  
  // Think time - simulate user behavior (1-3 seconds between requests)
  sleep(Math.random() * 2 + 1);
}

// Helper function to select endpoint based on weights
function selectWeightedEndpoint(endpoints) {
  const random = Math.random();
  let cumulativeWeight = 0;
  
  for (const endpoint of endpoints) {
    cumulativeWeight += endpoint.weight;
    if (random <= cumulativeWeight) {
      return endpoint;
    }
  }
  
  return endpoints[0]; // fallback
}

// Setup function - runs once before test
export function setup() {
  console.log('='.repeat(60));
  console.log('SmartScale K3s Autoscaler - Load Test');
  console.log('='.repeat(60));
  console.log(`Target URL: ${BASE_URL}`);
  console.log(`Test Duration: ${TEST_DURATION}`);
  console.log(`Expected Behavior:`);
  console.log(`  - At 50 VUs: Baseline load (2 nodes should suffice)`);
  console.log(`  - At 200 VUs: High load - should trigger scale-up`);
  console.log(`  - Expected: CPU > 70% → Lambda scales up within 3 min`);
  console.log(`  - After spike: CPU < 30% → Lambda scales down after 10 min`);
  console.log('='.repeat(60));
  
  // Verify demo app is accessible
  const healthCheck = http.get(`${BASE_URL}/health`);
  if (healthCheck.status !== 200) {
    throw new Error(`Demo app not accessible at ${BASE_URL}/health`);
  }
  
  return { startTime: Date.now() };
}

// Teardown function - runs once after test
export function teardown(data) {
  const testDuration = (Date.now() - data.startTime) / 1000;
  
  console.log('='.repeat(60));
  console.log('Load Test Complete');
  console.log('='.repeat(60));
  console.log(`Total Duration: ${testDuration.toFixed(2)} seconds`);
  console.log(`\nNext Steps:`);
  console.log(`  1. Check Grafana dashboard for cluster metrics`);
  console.log(`  2. Review CloudWatch logs for Lambda scaling decisions`);
  console.log(`  3. Verify node count increased during spike`);
  console.log(`  4. Wait 10+ minutes and check for scale-down`);
  console.log('='.repeat(60));
}

// Stage-specific scenario (optional - for custom behavior per stage)
export function handleSummary(data) {
  return {
    'summary.json': JSON.stringify(data, null, 2),
    stdout: textSummary(data, { indent: ' ', enableColors: true }),
  };
}

function textSummary(data, options) {
  // Custom summary formatting
  const indent = options.indent || '';
  const enableColors = options.enableColors !== false;
  
  let summary = '\n' + indent + '='.repeat(60) + '\n';
  summary += indent + 'Test Results Summary\n';
  summary += indent + '='.repeat(60) + '\n';
  
  // Request metrics
  summary += indent + `\nTotal Requests: ${data.metrics.http_reqs.values.count}\n`;
  summary += indent + `Request Rate: ${data.metrics.http_reqs.values.rate.toFixed(2)} req/s\n`;
  
  // Response time metrics
  summary += indent + `\nResponse Time:\n`;
  summary += indent + `  p50: ${data.metrics.http_req_duration.values['p(50)'].toFixed(2)}ms\n`;
  summary += indent + `  p95: ${data.metrics.http_req_duration.values['p(95)'].toFixed(2)}ms\n`;
  summary += indent + `  p99: ${data.metrics.http_req_duration.values['p(99)'].toFixed(2)}ms\n`;
  
  // Error metrics
  const failedReqs = data.metrics.http_req_failed ? data.metrics.http_req_failed.values.rate : 0;
  summary += indent + `\nError Rate: ${(failedReqs * 100).toFixed(2)}%\n`;
  
  // Threshold evaluation
  summary += indent + `\nThresholds:\n`;
  for (const [name, details] of Object.entries(data.metrics)) {
    if (details.thresholds) {
      for (const [threshold, result] of Object.entries(details.thresholds)) {
        const status = result.ok ? '✓ PASS' : '✗ FAIL';
        summary += indent + `  ${status}: ${threshold}\n`;
      }
    }
  }
  
  summary += indent + '='.repeat(60) + '\n';
  
  return summary;
}
