# node-fleet — Troubleshooting Guide

---

## Quick Diagnostics

```bash
# Check cluster state
kubectl get nodes -o wide
kubectl get pods --all-namespaces

# Check autoscaler state
aws dynamodb get-item \
  --table-name k3s-autoscaler-state \
  --key '{"cluster_id":{"S":"node-fleet-prod"}}' \
  --region ap-southeast-1 | jq .

# Stream Lambda logs
aws logs tail /aws/lambda/node-fleet-cluster-autoscaler --follow

# Check EventBridge status
aws events describe-rule \
  --name node-fleet-autoscaler-trigger \
  --region ap-southeast-1 | jq '.State'

# Test Lambda manually
aws lambda invoke \
  --function-name node-fleet-cluster-autoscaler \
  --payload '{"source":"manual-test"}' \
  /tmp/lambda-out.json && cat /tmp/lambda-out.json
```

---

## Issue Reference

### 1. Lambda Can't Reach Prometheus

**Symptom**: `ConnectionError: HTTPConnectionPool(host='10.0.11.x', port=30090)` or `requests.exceptions.ConnectTimeout`

**Cause**: SG missing inbound :30090 from Lambda SG, OR Lambda not in correct VPC/subnet.

**Fix**:
```bash
# Verify Lambda is in VPC
aws lambda get-function-configuration \
  --function-name node-fleet-cluster-autoscaler | jq '.VpcConfig'

# Add missing SG rule (if not present)
SG_MASTER=$(aws ec2 describe-security-groups \
  --filters "Name=tag:Name,Values=sg-master" \
  --query 'SecurityGroups[0].GroupId' --output text)
SG_LAMBDA=$(aws ec2 describe-security-groups \
  --filters "Name=tag:Name,Values=sg-lambda" \
  --query 'SecurityGroups[0].GroupId' --output text)

aws ec2 authorize-security-group-ingress \
  --group-id $SG_MASTER \
  --protocol tcp --port 30090 \
  --source-group $SG_LAMBDA
```

---

### 2. Workers Not Joining Cluster

**Symptom**: EC2 instances launch but never appear in `kubectl get nodes`. SSM logs: `Failed to find supervisor.conf` or `Error: token mismatch`.

**Cause**: K3s join token missing/wrong in Secrets Manager at boot. Userdata runs once — failed token fetch = worker never joins.

**Fix**:
```bash
# Check token in Secrets Manager
aws secretsmanager get-secret-value \
  --secret-id node-fleet/k3s-token \
  --query SecretString --output text

# Verify it matches master token
ssh -i node-fleet-key.pem ubuntu@$MASTER_IP \
  "sudo cat /var/lib/rancher/k3s/server/node-token"

# If mismatch: update secret
TOKEN=$(ssh -i node-fleet-key.pem ubuntu@$MASTER_IP \
  "sudo cat /var/lib/rancher/k3s/server/node-token")
aws secretsmanager put-secret-value \
  --secret-id node-fleet/k3s-token \
  --secret-string "$TOKEN"

# Terminate broken workers and let Lambda relaunch them
```

---

### 3. DynamoDB Lock Stuck

**Symptom**: `ConditionalCheckFailedException` every invocation, scaling never happens. Lambda crashed leaving `scaling_in_progress=true`.

**Cause**: Lambda crashed (timeout/exception) without releasing lock. Auto-clears at 360s — force-release if urgent:

**Fix**:
```bash
# Check lock state
aws dynamodb get-item \
  --table-name k3s-autoscaler-state \
  --key '{"cluster_id":{"S":"node-fleet-prod"}}' \
  --projection-expression 'scaling_in_progress, lock_acquired_at, lock_expiry' \
  --region ap-southeast-1

# Manual release (only if sure no Lambda is currently scaling)
aws dynamodb update-item \
  --table-name k3s-autoscaler-state \
  --key '{"cluster_id":{"S":"node-fleet-prod"}}' \
  --update-expression 'REMOVE scaling_in_progress, lock_acquired_at, lock_expiry' \
  --region ap-southeast-1

echo "Lock released"
```

---

### 4. Lambda Timeout During Scaling

**Symptom**: CloudWatch shows `Task timed out after 60.00 seconds`. Instances may be launching.

**Cause**: Lambda hit 60s limit. Common: Prometheus slow, EC2 RunInstances slow, node polling too long.

**Fix**:
```bash
# 1. Immediately disable EventBridge to stop repeated invocations
aws events disable-rule --name node-fleet-autoscaler-trigger --region ap-southeast-1

# 2. Check if instances are still launching
aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=node-fleet" "Name=instance-state-name,Values=pending,running" \
  --query 'Reservations[*].Instances[*].[InstanceId,State.Name,LaunchTime]' \
  --output table

# 3. Check if Prometheus is responding
curl -u prometheus:<password> http://$MASTER_IP:30090/-/healthy

# 4. Release stale lock (lock auto-clears at 360s, but manual if urgent)
aws dynamodb update-item \
  --table-name k3s-autoscaler-state \
  --key '{"cluster_id":{"S":"node-fleet-prod"}}' \
  --update-expression 'REMOVE scaling_in_progress, lock_acquired_at, lock_expiry' \
  --region ap-southeast-1

# 5. Fix the root cause, then re-enable
aws events enable-rule --name node-fleet-autoscaler-trigger --region ap-southeast-1
```

---

### 5. Windows Build Fails on Lambda

**Symptom**: `Unable to import module 'autoscaler': No module named '_cffi_backend'` or `cannot import name 'AES'`.

**Cause**: `pip install` on Windows builds Windows-native `.pyd` files. Lambda runs Linux — crashes.

**Fix**:
```bash
cd lambda

# Remove bad builds
rm -rf *.pyd *.egg-info

# Install with Linux-compatible wheels
pip install \
  --platform manylinux2014_x86_64 \
  --only-binary=:all: \
  --target=. \
  cryptography paramiko

pip install -r requirements.txt --target=. \
  --ignore-installed cryptography paramiko

zip -r function.zip . --exclude "*.pyc" "__pycache__/*" "venv/*" "tests/*"
aws lambda update-function-code \
  --function-name node-fleet-cluster-autoscaler \
  --zip-file fileb://function.zip
```

---

### 6. Prometheus Shows 0 Metrics

**Symptom**: Grafana "No data", Lambda logs `cpu=None`.

**Cause A**: `node-exporter` DaemonSet not deployed.
**Cause B**: `kube-state-metrics` not running.
**Cause C**: `static_configs` targets have wrong IPs.

**Fix**:
```bash
# Check Prometheus targets
curl -u prometheus:<password> http://$MASTER_IP:30090/api/v1/targets | \
  jq '.data.activeTargets[] | {job: .labels.job, health: .health, lastError: .lastError}'

# Deploy node-exporter if missing
kubectl apply -f gitops/infrastructure/prometheus-deployment.yaml

# Check pods
kubectl get pods -n monitoring

# Verify node-exporter running on workers
kubectl get daemonset -n monitoring node-exporter

# If static_configs targets wrong, get current worker IPs
kubectl get nodes -o jsonpath='{.items[*].status.addresses[?(@.type=="InternalIP")].address}'
# Update prometheus.yml with correct IPs
```

---

### 7. Grafana Dashboards Blank

**Symptom**: Grafana loads but panels show "No data source found" or blank.

**Cause**: Datasources/dashboards ConfigMap not applied.

**Fix**:
```bash
# Redeploy monitoring stack (creates all ConfigMaps)
bash scripts/deploy_monitoring.sh

# Verify ConfigMaps exist
kubectl get configmap -n monitoring

# Check Grafana pod logs
kubectl logs -n monitoring deployment/grafana

# Restart Grafana to pick up new ConfigMaps
kubectl rollout restart deployment/grafana -n monitoring
kubectl rollout status deployment/grafana -n monitoring

# Verify datasource
curl -u admin:<password> http://$MASTER_IP:30030/api/datasources | jq '.[].name'
```

---

### 8. Scale-Down Not Triggering

**Symptom**: CPU/memory low >15min, no scale-down event.

**Cause A**: Cooldown active (600s after last scale-down).
**Cause B**: Window incomplete (need 5 consecutive low readings = 10min).
**Cause C**: One condition briefly failed (e.g., memory spiked >50%).

**Diagnose**:
```bash
# Check last scale time
aws dynamodb get-item \
  --table-name k3s-autoscaler-state \
  --key '{"cluster_id":{"S":"node-fleet-prod"}}' \
  --query 'Item.{last_scale_time:last_scale_time,last_scale_action:last_scale_action}' \
  --region ap-southeast-1

# Check recent Lambda decisions in CloudWatch
aws logs filter-log-events \
  --log-group-name /aws/lambda/node-fleet-cluster-autoscaler \
  --filter-pattern '"scale_down" OR "cooldown" OR "insufficient"' \
  --start-time $(date -d '30 minutes ago' +%s000)
```

---

### 9. Spot Interruption Causes Downtime

**Symptom**: Worker terminates unexpectedly, pods restart, brief latency spike.

**Cause**: 2-min warning window too short if drain >90s.

**Fix**:
```bash
# Check spot interruption logs
aws logs filter-log-events \
  --log-group-name /aws/lambda/node-fleet-cluster-autoscaler \
  --filter-pattern '"spot_interruption"'

# Verify EventBridge rule for spot interruptions exists
aws events list-rules --name-prefix spot-interruption --region ap-southeast-1

# Reduce demo app terminationGracePeriodSeconds if >90s
kubectl patch deployment demo-app -p '{"spec":{"template":{"spec":{"terminationGracePeriodSeconds":60}}}}'

# Add more On-Demand workers temporarily
# Adjust SPOT_PERCENTAGE env var in Lambda config
aws lambda update-function-configuration \
  --function-name node-fleet-cluster-autoscaler \
  --environment "Variables={SPOT_PERCENTAGE=50,...}"
```

---

### 10. Drain Timeout / Node Not Terminating

**Symptom**: Node stuck in `SchedulingDisabled`. Lambda logs `Drain timed out after 300s`.

**Cause**: Pod with long `terminationGracePeriodSeconds` blocking drain.

**Fix**:
```bash
# Find the node
kubectl get nodes

# See what pods are blocking drain
kubectl get pods --all-namespaces \
  --field-selector spec.nodeName=<node-name> | grep -v DaemonSet

# If safe to force: force-terminate the blocking pod
kubectl delete pod <pod-name> -n <namespace> --grace-period=0 --force

# Un-cordon the node if aborting drain
kubectl uncordon <node-name>

# Clean up DynamoDB drain state if needed
aws dynamodb update-item \
  --table-name k3s-autoscaler-state \
  --key '{"cluster_id":{"S":"node-fleet-prod"}}' \
  --update-expression 'SET draining_instances = :empty' \
  --expression-attribute-values '{":empty":{"L":[]}}' \
  --region ap-southeast-1
```

---

### 11. EC2 Quota Exceeded

**Symptom**: `ClientError: An error occurred (InstanceLimitExceeded)`. No new nodes launch.

**Fix**:
```bash
# Check current instance count
aws ec2 describe-instances \
  --filters "Name=instance-state-name,Values=running" \
  --query 'length(Reservations[*].Instances[])'

# Request quota increase (takes hours)
aws service-quotas request-service-quota-increase \
  --service-code ec2 \
  --quota-code L-1216C47A \
  --desired-value 20

# Immediate workaround: scale down unused instances
# Or: switch to different instance type with available capacity
```

---

## Common Log Patterns

| Log Message | Meaning | Action |
|-------------|---------|--------|
| `"Lock acquired"` | Lock success | Normal |
| `"ConditionalCheckFailedException"` | Lock held by concurrent Lambda | Normal or stuck lock |
| `"Decision: none, reason: cooldown"` | In cooldown window | Normal |
| `"Decision: none, reason: stable"` | Metrics below thresholds | Normal |
| `"Skipping node: StatefulSet pod"` | Critical pod protection | Normal |
| `"Drain validated"` | Drain success, will terminate | Normal |
| `"drained keyword missing"` | Drain output invalid | Check SSM logs |
| `"Prometheus unreachable"` | Can't fetch metrics | Check VPC/SG, Prometheus pod |
| `"Task timed out"` | Lambda >60s | Disable EventBridge, investigate |
| `"InstanceLimitExceeded"` | EC2 quota hit | Request increase |
