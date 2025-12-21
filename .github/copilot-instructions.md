# SmartScale K3s Autoscaler - AI Agent Instructions

## Project Overview

This is a custom K3s autoscaler for AWS that reduces infrastructure costs by 40-50% through intelligent, automated scaling. The system uses AWS Lambda to monitor Prometheus metrics and dynamically scale EC2 worker nodes based on real-time cluster utilization.

**Current Status**: Planning/Design phase - implementation structure is defined but code artifacts don't exist yet.

## Architecture Fundamentals

### Core Data Flow
1. EventBridge triggers Lambda every 2 minutes
2. Lambda queries Prometheus for CPU/memory/pending pods metrics  
3. DynamoDB provides distributed locking to prevent race conditions
4. EC2 instances launch/terminate based on scaling logic
5. New workers auto-join K3s cluster using token from Secrets Manager
6. Slack notifications sent via SNS for all scaling events

### Scaling Decision Logic

**Scale UP** when ANY condition true:
- CPU > 70% for 3+ minutes
- Pending pods exist for 3+ minutes  
- Memory > 75% cluster-wide

**Scale DOWN** when ALL conditions true:
- CPU < 30% for 10+ minutes
- No pending pods
- Memory < 50%

**Constraints**: Min 2 nodes, Max 10 nodes. Scale-up adds 1-2 nodes; scale-down removes 1 node. Cooldowns: 5min after scale-up, 10min after scale-down.

## Project Structure & Conventions

### Directory Organization (Planned)
```
pulumi/       - Infrastructure as Code (Python) - all AWS resources
lambda/       - Python 3.11 autoscaler logic (metrics, EC2, state, K3s ops)
k3s/          - Shell scripts for master/worker setup, Prometheus configs
monitoring/   - Grafana dashboards (JSON), CloudWatch alarms, alert rules
demo-app/     - Flask app with Dockerfile and K8s manifests for testing
tests/        - k6 load tests, scale-up/down test scripts
docs/         - Detailed architecture, algorithms, runbooks
```

### Key Technology Decisions

**Why Pulumi (not Terraform)**: Python-native IaC matches Lambda runtime, type safety, easier testing.

**Why DynamoDB for state**: Conditional writes prevent concurrent Lambda scaling operations (race condition mitigation). Schema: `{cluster_id, node_count, last_scale_time, scaling_in_progress}`.

**Why Secrets Manager**: K3s join token must be securely retrievable by worker UserData scripts during EC2 launch.

**Why EventBridge (not cron)**: Serverless, integrates natively with Lambda, can dynamically adjust intervals later.

## Critical Implementation Details

### Lambda Function Structure (lambda/)

- `autoscaler.py`: Main handler orchestrating the 5-step flow (query → lock → decide → scale → notify)
- `metrics_collector.py`: Executes PromQL queries against Prometheus HTTP API (`/api/v1/query`)
- `ec2_manager.py`: Launch templates, instance creation/termination, health checks
- `state_manager.py`: DynamoDB conditional writes/reads with lock acquisition/release logic
- `k3s_helper.py`: Node drain operations (`kubectl drain`), wait for "Ready" status
- `slack_notifier.py`: SNS → Slack webhook integration with structured messages

### Prometheus Queries (Use These)
```promql
CPU:     avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100
Memory:  (1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100
Pending: sum(kube_pod_status_phase{phase="Pending"})
Nodes:   count(kube_node_info)
```

### Worker Auto-Join Pattern (k3s/worker-userdata.sh)

Workers must retrieve K3s token from Secrets Manager and master IP (from tags or Pulumi output), then execute:
```bash
curl -sfL https://get.k3s.io | K3S_URL=https://<master-ip>:6443 K3S_TOKEN=<token> sh -
```

Master IP resolution: Tag EC2 instances with `Role: k3s-master` and query via AWS CLI in UserData.

### Scale-Down Safety Requirements

Before terminating a node:
1. Check for critical system pods (kube-system namespace)
2. Execute `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=5m`
3. Wait for drain completion or timeout
4. Only terminate if drain succeeds AND no PodDisruptionBudget violations

## Development Workflow Commands

### Infrastructure Management
```bash
cd pulumi
pulumi up                    # Deploy/update all AWS resources
pulumi stack output masterIp # Get master node IP after deployment
pulumi destroy               # Tear down environment
```

### K3s Cluster Setup
```bash
cd k3s
./master-setup.sh            # Initialize master node (run on EC2)
kubectl get nodes            # Verify cluster state
kubectl apply -f prometheus-deployment.yaml  # Deploy Prometheus
```

### Lambda Development
```bash
cd lambda
pip install -r requirements.txt -t .  # Package dependencies for Lambda
zip -r function.zip .                  # Create deployment package
aws lambda update-function-code --function-name autoscaler --zip-file fileb://function.zip
```

### Testing
```bash
cd tests
k6 run load-test.js --vus 100 --duration 5m  # Simulate traffic spike
./test-scale-up.sh           # Verify autoscaling triggers
./test-scale-down.sh         # Verify safe node removal
```

## Common Pitfalls & Solutions

### DynamoDB Race Condition Prevention
Always use `ConditionExpression` with `attribute_not_exists(scaling_in_progress)` when acquiring locks. If lock acquisition fails, Lambda should exit gracefully and retry on next invocation.

### Lambda Timeout Handling
If Lambda times out mid-scaling (e.g., waiting for EC2 launch), next invocation must check DynamoDB for incomplete operations and either complete or rollback. Never leave locks stuck.

### Prometheus Connectivity
Prometheus runs as a pod on master node. Expose via NodePort (not LoadBalancer to save costs). Lambda must have VPC connectivity to master node's private IP on port 30090 (or configured NodePort).

### Worker Node Join Failures
If worker doesn't join within 5 minutes after EC2 launch, check:
1. Security group allows 6443/tcp from worker to master
2. K3s token in Secrets Manager is valid (doesn't expire)
3. Master node IP resolution in UserData script succeeded
4. CloudWatch logs for UserData script execution errors

## Security Requirements

- **No hardcoded credentials**: Use IAM roles exclusively
- **Secrets Manager**: Store K3s token with encryption at rest
- **Security Groups**: Master allows 6443/tcp from workers only; Prometheus NodePort restricted to Lambda's VPC
- **IAM Least Privilege**: Lambda role needs only: `ec2:RunInstances`, `ec2:TerminateInstances`, `dynamodb:PutItem`, `dynamodb:GetItem`, `secretsmanager:GetSecretValue`, `sns:Publish`, `logs:CreateLogGroup/Stream/Events`

## Code Patterns to Follow

### Error Handling in Lambda
Always wrap scaling operations in try/except with CloudWatch logging and Slack failure notifications. Example:
```python
try:
    metrics = query_prometheus()
    decision = evaluate_scaling(metrics)
    if decision == "scale_up":
        acquire_lock_or_exit()
        launch_instances()
        update_state()
        notify_slack("Scale-up initiated")
except Exception as e:
    logger.error(f"Scaling failed: {e}")
    notify_slack(f"🔴 Scaling error: {e}")
    raise  # Let Lambda retry
```

### Pulumi Resource Organization
Separate resources by concern (vpc.py, ec2.py, lambda_function.py, etc.). Export critical values:
```python
pulumi.export("masterIp", master_instance.private_ip)
pulumi.export("prometheusUrl", f"http://{master_instance.private_ip}:30090")
```

### Testing Strategy
Unit tests (pytest) for scaling logic. Integration tests simulate Prometheus responses. Load tests (k6) verify end-to-end autoscaling under realistic traffic patterns (gradual ramp, flash sale spike, sustained load).

## Monitoring & Observability

### Key CloudWatch Metrics to Create
- `AutoscalerInvocations` (count)
- `ScaleUpEvents` (count, dimension: reason)
- `ScaleDownEvents` (count)
- `ScalingFailures` (count, dimension: error_type)
- `NodeJoinLatency` (milliseconds)
- `LambdaExecutionTime` (milliseconds)

### Slack Notification Format
Use structured blocks with emojis:
- 🟢 Scale-up: "Added 2 nodes (CPU: 78%) → Total: 5 nodes"
- 🔵 Scale-down: "Removed 1 node (CPU: 25%) → Total: 3 nodes"
- 🔴 Failure: "Scale-up failed: EC2 quota exceeded"
- ⚠️ Warning: "At max capacity (10 nodes), cannot scale further"

## Future Extensions (Not Implemented Yet)

The README mentions optional bonus features:
- Multi-AZ worker distribution for resilience
- Spot instance support with interruption handling
- Predictive scaling based on historical trends
- Custom app metrics (queue depth, API latency)
- GitOps with FluxCD/ArgoCD

Don't implement these unless explicitly requested - focus on core autoscaling functionality first.
