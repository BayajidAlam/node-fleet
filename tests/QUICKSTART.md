# Quick Start: Testing SmartScale

## Installation

```bash
# Install test dependencies
cd tests
npm install

# Install k6 for load testing
brew install k6         # macOS
sudo apt install k6     # Ubuntu
```

## Run All Tests

```bash
# Unit + Integration tests
cd tests
npm test

# With coverage
npm run test:coverage

# Load tests
k6 run load/load-test.js
```

## Test Categories

### 1. Unit Tests (TypeScript/Jest)

```bash
npm run test:unit

# Tests:
# ✓ scale up when CPU > 70%
# ✓ scale up when pending pods exist
# ✓ scale down when CPU < 30%
# ✓ no action during cooldown
# ✓ min/max node constraints
# ✓ DynamoDB lock management
# ✓ Prometheus unavailable fallback
```

### 2. Integration Tests (TypeScript/Jest + LocalStack)

```bash
# Start LocalStack
docker run -d -p 4566:4566 localstack/localstack

# Run tests
npm run test:integration

# Tests:
# ✓ Lambda invokes EC2 with correct parameters
# ✓ DynamoDB state updates
# ✓ Secrets Manager retrieves K3s token
# ✓ Complete scale-up flow
```

### 3. Load Tests (k6)

```bash
# Gradual ramp
k6 run load/load-test.js

# Flash sale spike
k6 run load/load-test-flash-sale.js

# Sustained load (30 min)
k6 run load/load-test-sustained.js
```

### 4. Manual Tests (Bash)

```bash
chmod +x manual/*.sh

# Scale-up validation
./manual/test-scale-up.sh

# Scale-down validation
./manual/test-scale-down.sh
```

### 5. Failure Scenarios (Bash)

```bash
chmod +x scenarios/*.sh

# EC2 quota exceeded
./scenarios/test-quota-exceeded.sh

# Prometheus unavailable
./scenarios/test-prometheus-down.sh

# Node join failure
./scenarios/test-node-join-failure.sh

# Drain timeout
./scenarios/test-drain-timeout.sh
```

## Expected Results

### Unit Tests

```
Test Suites: 2 passed, 2 total
Tests:       8 passed, 8 total
Coverage:    95.2% statements, 92.1% branches
Time:        2.34s
```

### Integration Tests

```
Test Suites: 1 passed, 1 total
Tests:       4 passed, 4 total
Time:        8.52s (LocalStack)
```

### Load Test (k6)

```
http_req_duration..........: avg=450ms  p(95)=1200ms
http_req_failed............: 0.00%  (0/12450)
http_reqs..................: 12450  20.7/s

Autoscaler Response:
- Initial: 2 nodes
- After 3 min: 4 nodes (scale-up)
- After 5 min: 6 nodes (continued scale-up)
- After load drop: 4 nodes (scale-down after 10 min cooldown)
```

### Manual Scale-Up Test

```
[10:00:00] Nodes: 2 | Avg CPU: 35% | Pending: 0
[10:02:00] Nodes: 2 | Avg CPU: 78% | Pending: 5  ← High CPU detected
[10:04:00] Nodes: 2 | Avg CPU: 82% | Pending: 8
[10:06:00] Nodes: 3 | Avg CPU: 65% | Pending: 2  ← Scale-up! +1 node
[10:08:00] Nodes: 4 | Avg CPU: 48% | Pending: 0  ← Scale-up! +1 node

✅ Scale-up detected! New nodes: 4 (was 2)
```

### Manual Scale-Down Test

```
[10:00:00] Nodes: 4 | Avg CPU: 28% | Pending: 0  ← Low CPU detected
[10:10:00] Nodes: 4 | Avg CPU: 22% | Pending: 0  ← 10 min sustained
[10:12:00] Nodes: 3 | Avg CPU: 25% | Pending: 0  ← Scale-down! -1 node

✅ Scale-down detected! Nodes: 3 (was 4)
```

## Troubleshooting

### `npm test` fails with module errors

```bash
rm -rf node_modules package-lock.json
npm install
```

### LocalStack integration tests timeout

```bash
# Check LocalStack is running
docker ps | grep localstack

# Restart if needed
docker restart localstack

# Check logs
docker logs localstack
```

### k6 load test can't reach demo app

```bash
# Verify demo app is deployed
kubectl get pods -n default | grep demo-app

# Check service
kubectl get svc demo-app

# Get master IP
export MASTER_IP=$(pulumi stack output masterIp)
curl http://$MASTER_IP:30080/health

# Then run k6
k6 run load/load-test.js
```

### Manual tests don't trigger scaling

```bash
# Check Lambda is running
aws lambda get-function --function-name k3s-autoscaler

# Check EventBridge rule
aws events list-rules | grep k3s

# Manually invoke Lambda
aws lambda invoke --function-name k3s-autoscaler /tmp/test.json
cat /tmp/test.json

# Check logs
aws logs tail /aws/lambda/k3s-autoscaler --follow
```

### Coverage below threshold (85%)

```bash
# Generate HTML report
npm run test:coverage

# Open in browser
open coverage/lcov-report/index.html

# Identify untested code paths and add tests
```

## CI/CD Integration

### GitHub Actions

Add to `.github/workflows/test.yml`:

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - uses: actions/setup-node@v3
        with:
          node-version: "18"

      - name: Start LocalStack
        run: |
          docker run -d -p 4566:4566 \
            -e SERVICES=lambda,dynamodb,ec2,secretsmanager \
            localstack/localstack
          sleep 10

      - name: Run tests
        run: |
          cd tests
          npm install
          npm run test:ci

      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          files: ./tests/coverage/lcov.info
```

## Quick Reference

| Test Type         | Command                                 | Duration  | Purpose                    |
| ----------------- | --------------------------------------- | --------- | -------------------------- |
| Unit              | `npm run test:unit`                     | 2-3s      | Verify scaling logic       |
| Integration       | `npm run test:integration`              | 8-10s     | Verify AWS interactions    |
| Load (gradual)    | `k6 run load/load-test.js`              | 15 min    | Test gradual scaling       |
| Load (spike)      | `k6 run load/load-test-flash-sale.js`   | 13 min    | Test rapid scale-up        |
| Manual scale-up   | `./manual/test-scale-up.sh`             | 8-10 min  | Validate scale-up behavior |
| Manual scale-down | `./manual/test-scale-down.sh`           | 15-20 min | Validate scale-down safety |
| Quota exceeded    | `./scenarios/test-quota-exceeded.sh`    | 5 min     | Test quota limit handling  |
| Prometheus down   | `./scenarios/test-prometheus-down.sh`   | 5 min     | Test metric fallback       |
| Node join fail    | `./scenarios/test-node-join-failure.sh` | 10 min    | Test worker join failure   |
| Drain timeout     | `./scenarios/test-drain-timeout.sh`     | 15 min    | Test safe drain timeout    |

## Test Coverage Requirements

From REQUIREMENTS.md:

- [x] 8 unit test cases (scaling logic)
- [x] 4 integration tests (AWS services)
- [x] 1 load test (k6 with flash sale scenario)
- [x] 2 manual scripts (scale-up, scale-down)
- [x] 4 failure scenarios (quota, prometheus, join, drain)

**All requirements met with TypeScript/Jest framework.**

## Next Steps

1. Run `npm install` in tests directory
2. Start LocalStack: `docker run -d -p 4566:4566 localstack/localstack`
3. Run tests: `npm test`
4. Deploy infrastructure: `cd pulumi && pulumi up`
5. Run load tests: `k6 run load/load-test.js`
6. Validate manual tests once cluster is live

For detailed testing procedures, see [TESTING_GUIDE.md](TESTING_GUIDE.md).
