# node-fleet K3s Autoscaler - Test Suite

## Overview

This directory contains TypeScript-based tests for the node-fleet K3s Autoscaler. While the Lambda function is implemented in Python, tests are written in TypeScript to maintain consistency with the Pulumi infrastructure code.

## Technology Stack

- **Test Framework**: Jest 29.x
- **Language**: TypeScript 5.x
- **AWS Mocking**: aws-sdk-client-mock
- **Load Testing**: k6 (JavaScript/TypeScript)
- **Integration Testing**: LocalStack (optional)

## Directory Structure

```
tests/
├── unit/                    # Unit tests for scaling logic
│   ├── scaling-decision.test.ts
│   └── dynamodb-lock.test.ts
├── integration/             # Integration tests with AWS services
│   └── aws-services.test.ts
├── load/                    # k6 load test scripts
│   ├── load-test.js
│   ├── load-test-flash-sale.js
│   └── load-test-sustained.js
├── manual/                  # Manual test scripts
│   ├── test-scale-up.sh
│   └── test-scale-down.sh
├── scenarios/               # Failure scenario tests
│   ├── test-quota-exceeded.sh
│   ├── test-prometheus-down.sh
│   ├── test-node-join-failure.sh
│   └── test-drain-timeout.sh
├── package.json            # NPM dependencies
├── tsconfig.json           # TypeScript configuration
├── jest.config.js          # Jest configuration
└── setup.ts                # Test environment setup
```

## Installation

```bash
cd tests
npm install
```

## Running Tests

### All Tests

```bash
npm test
```

### Unit Tests Only

```bash
npm run test:unit
```

### Integration Tests (with LocalStack)

```bash
# Start LocalStack first
docker run -d -p 4566:4566 localstack/localstack

# Run integration tests
npm run test:integration
```

### With Coverage

```bash
npm run test:coverage
```

### Watch Mode (for development)

```bash
npm run test:watch
```

### CI/CD Mode

```bash
npm run test:ci
```

## Load Testing with k6

```bash
# Install k6
brew install k6  # macOS
# or
sudo apt install k6  # Ubuntu

# Run load tests
k6 run tests/load/load-test.js
k6 run tests/load/load-test-flash-sale.js
k6 run tests/load/load-test-sustained.js
```

## Manual Testing

Scale-up and scale-down tests require a live K3s cluster:

```bash
# Scale-up test
chmod +x tests/manual/test-scale-up.sh
./tests/manual/test-scale-up.sh

# Scale-down test
chmod +x tests/manual/test-scale-down.sh
./tests/manual/test-scale-down.sh
```

## Failure Scenario Tests

```bash
chmod +x tests/scenarios/*.sh

# Test EC2 quota exceeded
./tests/scenarios/test-quota-exceeded.sh

# Test Prometheus unavailable
./tests/scenarios/test-prometheus-down.sh

# Test worker node join failure
./tests/scenarios/test-node-join-failure.sh

# Test drain timeout
./tests/scenarios/test-drain-timeout.sh
```

## Test Coverage Goals

- **Unit Tests**: 85%+ line coverage
- **Integration Tests**: All AWS service interactions verified
- **Load Tests**: Autoscaling behavior validated under realistic traffic
- **Failure Scenarios**: All 4 scenarios from REQUIREMENTS.md

## Important Notes

### Python Lambda, TypeScript Tests

The Lambda function is implemented in Python (`lambda/autoscaler.py`), but tests are in TypeScript. This means:

1. **Unit tests** mock the Python Lambda's behavior using TypeScript
2. **Integration tests** invoke the actual Python Lambda via AWS SDK
3. **Load tests** trigger real autoscaling by generating traffic

### LocalStack vs Real AWS

- **LocalStack**: Fast, free, safe for CI/CD
- **Real AWS**: Required for end-to-end validation, costs apply

Always use LocalStack for development and CI/CD. Use real AWS only for final validation.

## Troubleshooting

### `npm install` fails

- Ensure Node.js 18+ is installed
- Delete `node_modules` and `package-lock.json`, retry

### Tests timeout

- Increase timeout in jest.config.js
- Check LocalStack is running (for integration tests)

### k6 not found

- Install k6: `brew install k6` or `sudo apt install k6`

### Coverage below threshold

- Review untested code paths in coverage report
- Open `coverage/lcov-report/index.html` in browser

## CI/CD Integration

GitHub Actions workflow example:

```yaml
- name: Run TypeScript tests
  run: |
    cd tests
    npm install
    npm run test:ci

- name: Upload coverage
  uses: codecov/codecov-action@v3
  with:
    files: ./tests/coverage/lcov.info
```

## Contributing

When adding new tests:

1. Follow existing naming conventions (`*.test.ts`)
2. Add appropriate mocks for AWS services
3. Ensure coverage threshold is met
4. Update this README if new test categories added

## References

- [Jest Documentation](https://jestjs.io/docs/getting-started)
- [aws-sdk-client-mock](https://github.com/m-radzikowski/aws-sdk-client-mock)
- [k6 Documentation](https://k6.io/docs/)
- [LocalStack Documentation](https://docs.localstack.cloud/)
