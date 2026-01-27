import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 20 }, // Ramp up to 20 users
    { duration: '5m', target: 50 },  // Stay at 50 users (Load Test)
    { duration: '30s', target: 0 },  // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p95<500'], // 95% of requests should be below 500ms
  },
};

export default function () {
  const res = http.get('http://13.212.80.238'); // Master Node LoadBalancer/Traefik IP
  check(res, { 'status was 200': (r) => r.status == 200 });
  sleep(1);
}
