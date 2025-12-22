# SmartScale Technology Stack

## Finalized Stack (December 2024)

### Infrastructure as Code (IaC)

- **Language**: TypeScript
- **Framework**: Pulumi
- **Cloud Provider**: AWS (ap-south-1 region)
- **Resources**: VPC, EC2, Lambda, DynamoDB, Secrets Manager, SNS, EventBridge

### Application Logic (Lambda Autoscaler)

- **Language**: Python 3.11
- **Runtime**: AWS Lambda
- **Dependencies**: boto3, requests
- **Key Modules**:
  - `autoscaler.py`: Main orchestration
  - `metrics_collector.py`: Prometheus queries
  - `ec2_manager.py`: EC2 instance operations
  - `state_manager.py`: DynamoDB state management
  - `k3s_helper.py`: K3s node operations
  - `slack_notifier.py`: SNS notifications

### Testing Framework

- **Unit/Integration Tests**: TypeScript with Jest 29.x
- **AWS Mocking**: aws-sdk-client-mock
- **Load Testing**: k6 (JavaScript/TypeScript)
- **Manual Scripts**: Bash
- **LocalStack**: For mock AWS integration testing

## Rationale

### Why TypeScript for Pulumi?

1. **Type Safety**: Catch infrastructure errors at compile-time
2. **IDE Support**: Better autocomplete and refactoring
3. **Consistency**: Matches test framework language
4. **Community**: Strong Pulumi TypeScript ecosystem

### Why Python for Lambda?

1. **REQUIREMENTS.md Specification**: Explicitly requires Python 3.11
2. **AWS Boto3**: Native, well-supported AWS SDK
3. **Simplicity**: Easier for ops teams to maintain
4. **Performance**: Sufficient for 2-minute interval execution
5. **Cold Start**: Minimal impact with EventBridge scheduling

### Why TypeScript for Tests?

1. **Consistency with IaC**: Same language as Pulumi
2. **Type Safety**: Catch test errors early
3. **Modern Tooling**: Jest, aws-sdk-client-mock
4. **Team Familiarity**: Align with infrastructure codebase

### Why LocalStack?

1. **Cost Savings**: No AWS charges during development
2. **Speed**: Instant feedback loop
3. **Safety**: No risk of accidentally affecting production
4. **CI/CD**: Runs in GitHub Actions without AWS credentials

## Project Structure

```
node-fleet/
├── pulumi/              # TypeScript IaC
│   ├── index.ts
│   ├── vpc.ts
│   ├── ec2.ts
│   ├── lambda.ts
│   ├── dynamodb.ts
│   └── package.json
│
├── lambda/              # Python 3.11 autoscaler
│   ├── autoscaler.py
│   ├── metrics_collector.py
│   ├── ec2_manager.py
│   ├── state_manager.py
│   ├── k3s_helper.py
│   ├── slack_notifier.py
│   └── requirements.txt
│
├── tests/               # TypeScript tests
│   ├── unit/
│   │   ├── scaling-decision.test.ts
│   │   └── dynamodb-lock.test.ts
│   ├── integration/
│   │   └── aws-services.test.ts
│   ├── load/
│   │   ├── load-test.js
│   │   └── load-test-flash-sale.js
│   ├── manual/
│   │   ├── test-scale-up.sh
│   │   └── test-scale-down.sh
│   ├── package.json
│   ├── tsconfig.json
│   └── jest.config.js
│
├── k3s/                 # Shell scripts
│   ├── master-setup.sh
│   ├── worker-userdata.sh
│   └── prometheus-deployment.yaml
│
├── monitoring/          # Grafana dashboards (JSON)
│   └── grafana-dashboards/
│
├── demo-app/            # Flask app (Python)
│   ├── Dockerfile
│   └── k8s/
│
└── docs/                # Markdown documentation
    ├── REQUIREMENTS.md
    ├── SOLUTION_ARCHITECTURE.md
    ├── TECHNOLOGY_SELECTION.md
    ├── TESTING_GUIDE.md
    └── COMPARISON_WITH_PROTOTYPE.md
```

## Development Workflow

### Infrastructure Deployment

```bash
cd pulumi
npm install
pulumi up
```

### Lambda Development

```bash
cd lambda
pip install -r requirements.txt
# Edit autoscaler.py
zip -r function.zip .
aws lambda update-function-code --function-name k3s-autoscaler --zip-file fileb://function.zip
```

### Testing

```bash
cd tests
npm install
npm test                    # Unit + integration
k6 run load/load-test.js    # Load testing
./manual/test-scale-up.sh   # Manual validation
```

## Dependencies

### Pulumi (TypeScript)

```json
{
  "@pulumi/pulumi": "^3.100.0",
  "@pulumi/aws": "^6.15.0",
  "typescript": "^5.3.3"
}
```

### Lambda (Python)

```txt
boto3==1.34.10
requests==2.31.0
```

### Tests (TypeScript)

```json
{
  "jest": "^29.7.0",
  "ts-jest": "^29.1.1",
  "aws-sdk-client-mock": "^3.0.1",
  "@aws-sdk/client-dynamodb": "^3.485.0",
  "@aws-sdk/client-ec2": "^3.485.0",
  "@aws-sdk/client-lambda": "^3.485.0"
}
```

## Runtime Requirements

- **Node.js**: 18+ (for Pulumi and tests)
- **Python**: 3.11 (for Lambda development)
- **kubectl**: 1.28+ (for K3s management)
- **k6**: v0.47+ (for load testing)
- **AWS CLI**: 2.x (for deployment)
- **Docker**: 24+ (for LocalStack and demo app)

## CI/CD Integration

GitHub Actions workflow uses all three languages:

```yaml
name: SmartScale CI/CD

on: [push, pull_request]

jobs:
  test-infrastructure:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: "18"
      - run: cd pulumi && npm install && pulumi preview

  test-lambda:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: "3.11"
      - run: cd lambda && pip install -r requirements.txt && pytest

  test-suite:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: "18"
      - run: docker run -d -p 4566:4566 localstack/localstack
      - run: cd tests && npm install && npm run test:ci
```

## Trade-offs & Considerations

### Multi-Language Approach

**Pros**:

- Use best tool for each job
- Leverage AWS Lambda's Python ecosystem
- Type safety for infrastructure and tests

**Cons**:

- Team must know TypeScript AND Python
- Separate dependency management (npm + pip)
- Different coding standards/linting

### Decision: Accept Complexity

The benefits of using specialized tools (TypeScript IaC, Python Lambda) outweigh the cost of managing two languages. The project structure clearly separates concerns, making it maintainable.

## Future Considerations

### Potential Migrations

- **Lambda to TypeScript**: If AWS SDK matures further
- **Tests to Python**: If team prefers single language
- **IaC to Terraform**: If organization standardizes

### No Current Plans

These are documented for awareness, not implementation.

## References

- [Pulumi TypeScript Guide](https://www.pulumi.com/docs/languages-sdks/typescript/)
- [AWS Lambda Python 3.11](https://docs.aws.amazon.com/lambda/latest/dg/lambda-python.html)
- [Jest Documentation](https://jestjs.io/docs/getting-started)
- [aws-sdk-client-mock](https://github.com/m-radzikowski/aws-sdk-client-mock)
- [k6 Load Testing](https://k6.io/docs/)
- [LocalStack AWS Emulation](https://docs.localstack.cloud/)
