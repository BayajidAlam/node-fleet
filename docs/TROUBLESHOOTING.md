# node-fleet K3s Autoscaler - Troubleshooting Guide

## Table of Contents

1. [Quick Diagnostic Commands](#quick-diagnostic-commands)
2. [Lambda Function Issues](#lambda-function-issues)
3. [K3s Cluster Issues](#k3s-cluster-issues)
4. [Prometheus & Monitoring Issues](#prometheus--monitoring-issues)
5. [Networking & Connectivity Issues](#networking--connectivity-issues)
6. [DynamoDB & State Management Issues](#dynamodb--state-management-issues)
7. [EC2 Instance Issues](#ec2-instance-issues)
8. [Emergency Procedures](#emergency-procedures)

---

## Quick Diagnostic Commands

### Check Overall System Health

```bash
# Check Lambda execution status
aws logs tail /aws/lambda/node-fleet-dev-autoscaler --since 10m --region ap-southeast-1

# Check K3s cluster nodes
kubectl get nodes -o wide

# Check Prometheus health
curl -s http://$(cat master-ip.txt):30090/api/v1/query?query=up | jq '.data.result[] | {job: .metric.job, status: .value[1]}'

# Check DynamoDB state
aws dynamodb get-item --table-name node-fleet-dev-state --key '{"cluster_id": {"S": "node-fleet-cluster"}}' --region ap-southeast-1 | jq '.Item'

# Check CloudWatch metrics (last 5 min)
aws cloudwatch get-metric-statistics \
  --namespace node-fleet \
  --metric-name CurrentNodeCount \
  --start-time $(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Average \
  --region ap-southeast-1
```

---

## Lambda Function Issues

### Issue 1: Lambda Function Not Executing

**Symptoms**:

- No logs in CloudWatch for 10+ minutes
- EventBridge rule shows no invocations

**Diagnosis**:

```bash
# Check EventBridge rule state
aws events describe-rule --name node-fleet-dev-autoscaler-schedule --region ap-southeast-1

# Check Lambda function state
aws lambda get-function --function-name node-fleet-dev-autoscaler --region ap-southeast-1 | jq '.Configuration.State'

# Check EventBridge target
aws events list-targets-by-rule --rule node-fleet-dev-autoscaler-schedule --region ap-southeast-1
```

**Solutions**:

```bash
# If EventBridge rule is DISABLED
aws events enable-rule --name node-fleet-dev-autoscaler-schedule --region ap-southeast-1

# If Lambda is in Failed state
aws lambda update-function-code \
  --function-name node-fleet-dev-autoscaler \
  --zip-file fileb://lambda/autoscaler.zip \
  --region ap-southeast-1

# Manually trigger Lambda to test
aws lambda invoke \
  --function-name node-fleet-dev-autoscaler \
  --payload '{}' \
  --region ap-southeast-1 \
  response.json && cat response.json
```

---

### Issue 2: Lambda Timeout (Execution > 60s)

**Symptoms**:

- CloudWatch logs show "Task timed out after 60.00 seconds"
- Scaling operations incomplete

**Diagnosis**:

```bash
# Check average execution duration
aws logs filter-log-events \
  --log-group-name /aws/lambda/node-fleet-dev-autoscaler \
  --start-time $(date -u -d '1 hour ago' +%s)000 \
  --filter-pattern "Duration" \
  --region ap-southeast-1 | grep Duration
```

**Solutions**:

```bash
# Increase Lambda timeout to 120s
aws lambda update-function-configuration \
  --function-name node-fleet-dev-autoscaler \
  --timeout 120 \
  --region ap-southeast-1

# Root causes to investigate:
# 1. Prometheus queries slow → Check Prometheus pod resource limits
# 2. kubectl drain timeout → Reduce drain timeout in code (300s → 180s)
# 3. DynamoDB lock contention → Check for stuck locks
```

---

### Issue 3: Lambda Cannot Connect to Prometheus

**Symptoms**:

- Error: "ConnectionError: HTTPConnectionPool(host='10.0.11.x', port=30090)"
- Lambda logs show Prometheus query failures

**Diagnosis**:

```bash
# Check Lambda VPC configuration
aws lambda get-function-configuration \
  --function-name node-fleet-dev-autoscaler \
  --region ap-southeast-1 | jq '.VpcConfig'

# Verify Lambda is in correct subnets (private subnets)
# Verify security groups allow outbound to master:30090

# Test connectivity from Lambda (create test function)
cat > /tmp/test-lambda.py << 'EOF'
import requests
def lambda_handler(event, context):
    try:
        response = requests.get('http://10.0.11.50:30090/api/v1/query?query=up', timeout=5)
        return {"statusCode": 200, "body": response.text}
    except Exception as e:
        return {"statusCode": 500, "body": str(e)}
EOF
```

**Solutions**:

```bash
# Fix 1: Update Lambda security group egress rules
LAMBDA_SG=$(aws lambda get-function-configuration --function-name node-fleet-dev-autoscaler --query 'VpcConfig.SecurityGroupIds[0]' --output text --region ap-southeast-1)

aws ec2 authorize-security-group-egress \
  --group-id $LAMBDA_SG \
  --protocol tcp \
  --port 30090 \
  --cidr 10.0.0.0/16 \
  --region ap-southeast-1

# Fix 2: Update master security group ingress rules
MASTER_SG=$(aws ec2 describe-instances \
  --filters "Name=tag:Role,Values=k3s-master" "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' \
  --output text \
  --region ap-southeast-1)

aws ec2 authorize-security-group-ingress \
  --group-id $MASTER_SG \
  --protocol tcp \
  --port 30090 \
  --source-group $LAMBDA_SG \
  --region ap-southeast-1
```

---

### Issue 4: Lambda Cannot Execute kubectl Commands

**Symptoms**:

- Error: "kubectl: command not found"
- Scale-down operations fail during drain

**Diagnosis**:

```bash
# Check if kubectl is bundled in Lambda package
unzip -l lambda/autoscaler.zip | grep kubectl

# Check Lambda execution role has SSH/SSM permissions
aws iam get-role-policy \
  --role-name node-fleet-lambda-autoscaler-role \
  --policy-name lambda-policy \
  --region ap-southeast-1
```

**Solutions**:

The Lambda function uses SSH to master node for kubectl commands. Ensure:

```bash
# 1. SSH key is accessible (use Secrets Manager or SSM Parameter Store)
# 2. Lambda has network connectivity to master's private IP
# 3. Master security group allows SSH (22/tcp) from Lambda SG

# Alternative: Use Kubernetes Python client instead of kubectl
pip install kubernetes
# Update lambda code to use kubernetes.client instead of subprocess kubectl
```

---

## K3s Cluster Issues

### Issue 1: Worker Nodes Not Joining Cluster

**Symptoms**:

- EC2 instances running but `kubectl get nodes` shows only master
- Worker count stays below MIN_NODES

**Diagnosis**:

```bash
# Get worker instance IDs
aws ec2 describe-instances \
  --filters "Name=tag:Role,Values=k3s-worker" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].[InstanceId,PrivateIpAddress,State.Name]' \
  --output table \
  --region ap-southeast-1

# SSH to worker and check logs
ssh -i ~/.ssh/node-fleet-key.pem ubuntu@<worker-private-ip>
sudo journalctl -u k3s-agent -n 100 --no-pager
```

**Common Error Messages**:

**Error 1**: `"Unable to connect to server: dial tcp 10.0.11.x:6443: i/o timeout"`

```bash
# Solution: Fix security group (allow 6443/tcp from workers to master)
WORKER_SG=$(aws ec2 describe-instances \
  --filters "Name=tag:Role,Values=k3s-worker" \
  --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' \
  --output text \
  --region ap-southeast-1)

aws ec2 authorize-security-group-ingress \
  --group-id $MASTER_SG \
  --protocol tcp \
  --port 6443 \
  --source-group $WORKER_SG \
  --region ap-southeast-1
```

**Error 2**: `"Node password rejected, contents of '/var/lib/rancher/k3s/agent/node-password' may not match server"`

```bash
# Solution: K3s token mismatch - update Secrets Manager
# On master node:
K3S_TOKEN=$(sudo cat /var/lib/rancher/k3s/server/node-token)

# On local machine:
aws secretsmanager update-secret \
  --secret-id node-fleet/k3s-token \
  --secret-string "$K3S_TOKEN" \
  --region ap-southeast-1

# On worker node (re-install K3s):
sudo systemctl stop k3s-agent
sudo rm -rf /var/lib/rancher/k3s/agent
curl -sfL https://get.k3s.io | K3S_URL=https://<master-private-ip>:6443 K3S_TOKEN=$K3S_TOKEN sh -
```

**Error 3**: `"Failed to find master IP"`

```bash
# Solution: EC2 tag missing or UserData script failed
# Manually join worker:
MASTER_PRIVATE_IP=$(aws ec2 describe-instances \
  --filters "Name=tag:Role,Values=k3s-master" "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' \
  --output text \
  --region ap-southeast-1)

K3S_TOKEN=$(aws secretsmanager get-secret-value \
  --secret-id node-fleet/k3s-token \
  --query SecretString \
  --output text \
  --region ap-southeast-1)

curl -sfL https://get.k3s.io | K3S_URL=https://$MASTER_PRIVATE_IP:6443 K3S_TOKEN=$K3S_TOKEN sh -
```

---

### Issue 2: Master Node Not Ready

**Symptoms**:

- `kubectl get nodes` shows master in NotReady state
- All kubectl commands fail

**Diagnosis**:

```bash
# SSH to master
ssh -i ~/.ssh/node-fleet-key.pem ubuntu@$(cat master-ip.txt)

# Check K3s service status
sudo systemctl status k3s

# Check K3s logs
sudo journalctl -u k3s -n 200 --no-pager

# Check node conditions
sudo kubectl describe node $(hostname)
```

**Solutions**:

```bash
# If K3s service crashed, restart it
sudo systemctl restart k3s
sudo systemctl enable k3s

# If CNI plugin issues (Flannel)
sudo kubectl delete pods -n kube-system -l app=flannel
# Wait for pods to recreate

# If etcd corruption (rare)
sudo k3s server --cluster-reset
# WARNING: This deletes all cluster data
```

---

## Prometheus & Monitoring Issues

### Issue 1: Prometheus Pod CrashLoopBackOff

**Symptoms**:

- `kubectl get pods -n monitoring` shows prometheus pod restarting
- Autoscaler cannot collect metrics

**Diagnosis**:

```bash
# Check pod status
kubectl describe pod -n monitoring -l app=prometheus

# Check pod logs
kubectl logs -n monitoring -l app=prometheus --tail=100

# Check persistent volume (if used)
kubectl get pvc -n monitoring
```

**Solutions**:

```bash
# Common cause: Out of memory
# Fix: Increase Prometheus memory limits
kubectl patch deployment prometheus -n monitoring -p '{"spec":{"template":{"spec":{"containers":[{"name":"prometheus","resources":{"limits":{"memory":"2Gi"},"requests":{"memory":"1Gi"}}}]}}}}'

# If PVC full, increase size or reduce retention
kubectl patch deployment prometheus -n monitoring -p '{"spec":{"template":{"spec":{"containers":[{"name":"prometheus","args":["--storage.tsdb.retention.time=3d"]}]}}}}'

# Delete and recreate if corrupted
kubectl delete deployment prometheus -n monitoring
kubectl apply -f k3s/prometheus-deployment.yaml
```

---

### Issue 2: Prometheus Metrics Missing

**Symptoms**:

- PromQL queries return empty results
- Grafana dashboards show "No Data"

**Diagnosis**:

```bash
# Check Prometheus targets
curl http://$(cat master-ip.txt):30090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health: .health}'

# Check specific metric exists
curl "http://$(cat master-ip.txt):30090/api/v1/query?query=up" | jq '.data.result'

# Check node-exporter pods
kubectl get pods -n kube-system -l app=node-exporter
```

**Solutions**:

```bash
# If node-exporter not running, deploy it
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: node-exporter
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app: node-exporter
  template:
    metadata:
      labels:
        app: node-exporter
    spec:
      hostNetwork: true
      hostPID: true
      containers:
      - name: node-exporter
        image: prom/node-exporter:latest
        ports:
        - containerPort: 9100
EOF

# Update Prometheus ConfigMap to scrape node-exporter
kubectl edit configmap prometheus-config -n monitoring
# Add job for node-exporter on port 9100

# Reload Prometheus config
kubectl rollout restart deployment prometheus -n monitoring
```

---

## Networking & Connectivity Issues

### Issue 1: NAT Gateway Charges Too High

**Symptoms**:

- AWS bill shows high NAT Gateway data transfer costs
- $50-100/month in NAT charges

**Diagnosis**:

```bash
# Check NAT Gateway metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/NATGateway \
  --metric-name BytesOutToDestination \
  --start-time $(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 86400 \
  --statistics Sum \
  --region ap-southeast-1
```

**Solutions**:

```bash
# Option 1: Use VPC Endpoints for AWS services (no NAT charges)
# Create endpoints for DynamoDB, Secrets Manager, EC2

aws ec2 create-vpc-endpoint \
  --vpc-id $(cat vpc-id.txt) \
  --service-name com.amazonaws.ap-southeast-1.dynamodb \
  --route-table-ids <private-route-table-ids> \
  --region ap-southeast-1

# Option 2: Deploy single NAT Gateway (instead of 2 AZs)
# Trade-off: Less resilient but saves $45/month

# Option 3: Use EC2 as NAT instance (t3.nano = $3/month vs $32/month NAT Gateway)
# See AWS documentation for NAT instance setup
```

---

## DynamoDB & State Management Issues

### Issue 1: DynamoDB Lock Stuck

**Symptoms**:

- Lambda logs: "Scaling already in progress" for 10+ minutes
- No actual scaling happening

**Diagnosis**:

```bash
# Check lock status
aws dynamodb get-item \
  --table-name node-fleet-dev-state \
  --key '{"cluster_id": {"S": "node-fleet-cluster"}}' \
  --region ap-southeast-1 | jq '.Item.scaling_in_progress, .Item.lock_acquired_at'
```

**Solutions**:

```bash
# Check if lock is truly stuck (> 5 minutes old)
LOCK_TIME=$(aws dynamodb get-item \
  --table-name node-fleet-dev-state \
  --key '{"cluster_id": {"S": "node-fleet-cluster"}}' \
  --query 'Item.lock_acquired_at.S' \
  --output text \
  --region ap-southeast-1)

echo "Lock acquired at: $LOCK_TIME"
echo "Current time: $(date -u +%Y-%m-%dT%H:%M:%S)"

# If stuck > 10 minutes, force release (EMERGENCY ONLY)
aws dynamodb update-item \
  --table-name node-fleet-dev-state \
  --key '{"cluster_id": {"S": "node-fleet-cluster"}}' \
  --update-expression "SET scaling_in_progress = :false REMOVE lock_acquired_at" \
  --expression-attribute-values '{":false": {"BOOL": false}}' \
  --region ap-southeast-1

# Investigate root cause:
# - Check if Lambda crashed mid-execution (CloudWatch logs)
# - Check if EC2 API calls timed out
# - Verify finally block in Lambda releases lock
```

---

## EC2 Instance Issues

### Issue 1: Spot Instance Interrupted Without Replacement

**Symptoms**:

- Worker count drops below MIN_NODES
- No new instances launched

**Diagnosis**:

```bash
# Check Spot interruption notices
aws ec2 describe-spot-instance-requests \
  --filters "Name=state,Values=closed" \
  --region ap-southeast-1 | jq '.SpotInstanceRequests[] | {InstanceId: .InstanceId, Status: .Status}'

# Check autoscaler logs for launch failures
aws logs filter-log-events \
  --log-group-name /aws/lambda/node-fleet-dev-autoscaler \
  --filter-pattern "RunInstances" \
  --region ap-southeast-1 | jq '.events[].message'
```

**Solutions**:

```bash
# If Spot capacity unavailable, autoscaler should fallback to On-Demand
# Check Lambda code has fallback logic:
# grep -A 10 "SpotInstanceType" lambda/ec2_manager.py

# Manually launch On-Demand replacement
aws ec2 run-instances \
  --launch-template LaunchTemplateId=$(pulumi stack output workerLaunchTemplateId) \
  --subnet-id $(pulumi stack output privateSubnet1aId) \
  --instance-market-options '{}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Project,Value=node-fleet},{Key=Role,Value=k3s-worker}]' \
  --region ap-southeast-1
```

---

## Emergency Procedures

### Emergency 1: Complete Cluster Outage

**Scenario**: All worker nodes down, master unreachable

**Recovery Steps**:

```bash
# Step 1: Check master instance state
aws ec2 describe-instance-status \
  --instance-ids $(aws ec2 describe-instances --filters "Name=tag:Role,Values=k3s-master" --query 'Reservations[0].Instances[0].InstanceId' --output text --region ap-southeast-1) \
  --region ap-southeast-1

# Step 2: If master stopped, start it
aws ec2 start-instances --instance-ids <master-instance-id> --region ap-southeast-1

# Step 3: Wait for master to be ready (5-10 min)
watch -n 10 "aws ec2 describe-instance-status --instance-ids <master-instance-id> --region ap-southeast-1 | jq '.InstanceStatuses[0].InstanceState.Name'"

# Step 4: Force launch MIN_NODES workers
for i in {1..2}; do
  aws ec2 run-instances \
    --launch-template LaunchTemplateId=$(pulumi stack output workerLaunchTemplateId) \
    --subnet-id $(pulumi stack output privateSubnet1aId) \
    --region ap-southeast-1
done

# Step 5: Verify cluster recovery
sleep 300  # Wait 5 min for nodes to join
kubectl get nodes
```

---

### Emergency 2: Autoscaler Stuck Scaling Up (At Max Capacity)

**Scenario**: Cluster at 10 nodes, still receiving scale-up triggers

**Recovery Steps**:

```bash
# Step 1: Identify root cause
kubectl top nodes  # Check if CPU truly high
kubectl get pods --all-namespaces | grep Pending  # Check pending pods

# Step 2: Manually scale problematic deployments
kubectl scale deployment <app-name> --replicas=5  # Reduce if too many replicas

# Step 3: Increase MAX_NODES temporarily (if justified)
aws lambda update-function-configuration \
  --function-name node-fleet-dev-autoscaler \
  --environment Variables={MAX_NODES=15,...} \
  --region ap-southeast-1

# Step 4: Investigate inefficient pods (high CPU/memory but low work)
kubectl describe pod <high-cpu-pod>
# Consider adding resource limits or optimizing application
```

---

### Emergency 3: Production Incident - Disable Autoscaler

**Scenario**: Autoscaler causing issues, need to stop all automation

**Recovery Steps**:

```bash
# Step 1: Disable EventBridge rule (stops Lambda triggers)
aws events disable-rule --name node-fleet-dev-autoscaler-schedule --region ap-southeast-1

# Step 2: Release any stuck DynamoDB locks
aws dynamodb update-item \
  --table-name node-fleet-dev-state \
  --key '{"cluster_id": {"S": "node-fleet-cluster"}}' \
  --update-expression "SET scaling_in_progress = :false REMOVE lock_acquired_at" \
  --expression-attribute-values '{":false": {"BOOL": false}}' \
  --region ap-southeast-1

# Step 3: Manually manage cluster
# Use AWS Console or CLI to launch/terminate instances as needed

# Step 4: Re-enable when safe
aws events enable-rule --name node-fleet-dev-autoscaler-schedule --region ap-southeast-1
```

---

## Contact & Support

For urgent production issues:

- **GitHub Issues**: https://github.com/BayajidAlam/node-fleet/issues
- **Email**: bayajidalam2001@gmail.com

For detailed architecture and deployment guides:

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
- [SCALING_ALGORITHM.md](SCALING_ALGORITHM.md)
