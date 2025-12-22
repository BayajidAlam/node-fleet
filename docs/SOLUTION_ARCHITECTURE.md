# SmartScale K3s Autoscaler - Solution Architecture & Implementation Strategy

## 🎯 Executive Summary

This document outlines the **cost-optimized, scalable solution** for building the SmartScale K3s autoscaler. Each component is designed with:

- **Minimal AWS costs** (<$100/month for full production setup)
- **Scalability** to handle 10,000+ concurrent users
- **High availability** (multi-AZ, fault-tolerant)
- **Operational simplicity** (minimal manual intervention)

**Target Cost Breakdown**:

- EC2 Instances (dynamic): $30-60/month (vs. $120/month fixed)
- Lambda: $2-5/month (15,000 invocations)
- DynamoDB: $1-2/month (on-demand, low traffic)
- CloudWatch: $3-5/month (custom metrics + logs)
- Secrets Manager: $2/month (3 secrets)
- ECR: $1/month (minimal storage)
- **Total**: $39-75/month (50-65% savings from baseline)

---

## 📐 Architecture Design Decisions

### Why K3s Over Standard Kubernetes?

- **50% lower resource usage** (512MB RAM vs. 1GB for control plane)
- **Single binary** installation (~60MB vs. hundreds of MB for kubeadm)
- **No etcd management** complexity (uses SQLite by default)
- **Perfect for edge/small clusters** (2-10 nodes)
- **Same K8s API** (100% compatible with kubectl, Helm)

### Why Lambda Over EC2-Based Autoscaler?

| Aspect         | Lambda                                  | EC2 (t3.micro)            |
| -------------- | --------------------------------------- | ------------------------- |
| Cost           | $0.20/million requests                  | $7/month (always running) |
| Scalability    | Auto-scales, no limits                  | Fixed capacity            |
| Maintenance    | Zero server management                  | OS patches, updates       |
| Cold start     | ~200ms (acceptable for 2-min intervals) | N/A                       |
| Execution time | 60s limit (sufficient)                  | Unlimited                 |

**Decision**: Lambda wins on cost and simplicity. For 15,000 monthly invocations: **$0.50/month vs. $7/month**.

### Why DynamoDB Over RDS/SQLite?

| Feature            | DynamoDB (On-Demand) | RDS (db.t3.micro)     | SQLite (on EC2)         |
| ------------------ | -------------------- | --------------------- | ----------------------- |
| Cost               | $1.25/million writes | $15/month minimum     | $0 (bundled)            |
| Scalability        | Infinite             | Limited by instance   | Limited by disk I/O     |
| Availability       | 99.99% SLA           | 99.95%                | Single point of failure |
| Conditional writes | Native support       | Complex (row locking) | No distributed locking  |
| Maintenance        | Zero                 | Backups, patches      | Manual                  |

**Decision**: DynamoDB for distributed locking (critical for race condition prevention) at **$1-2/month** vs. $15/month for RDS.

### Why Prometheus Over CloudWatch Metrics Only?

| Capability         | Prometheus                        | CloudWatch                |
| ------------------ | --------------------------------- | ------------------------- |
| K8s-native metrics | ✅ Excellent (kube-state-metrics) | ❌ Requires custom agents |
| PromQL queries     | ✅ Powerful, flexible             | ⚠️ Limited query language |
| Cost               | $0 (self-hosted)                  | $0.30 per metric/month    |
| Retention          | 7 days (configurable)             | 15 months (expensive)     |
| Granularity        | 15-second scrapes                 | 1-minute minimum          |

**Decision**: **Hybrid approach** - Prometheus for real-time K8s metrics (free), CloudWatch for Lambda logs and alarms (pay only for what you use).

### Why Pulumi TypeScript Over Terraform?

- **Type safety**: Catch errors at compile-time (vs. runtime in HCL)
- **Real programming language**: Loops, functions, conditionals (cleaner than HCL)
- **Same AWS SDK**: Familiar `aws.ec2.Instance` syntax
- **Better testing**: Use Jest/Mocha for unit tests
- **Faster feedback**: `pulumi preview` shows exact changes

**Cost impact**: None (both are free). TypeScript wins on developer experience.

---

## 🔧 Component-by-Component Solution

## 1. Metric Collection & Analysis (Prometheus + kube-state-metrics)

### Solution Design

**Architecture**:

```
K3s Nodes → node-exporter (9100) → Prometheus (scrapes every 15s)
            ↓ Metrics Collected:
            - CPU usage (all cores, all modes)
            - Memory (available, total, used)
            - Network I/O (receive/transmit bytes/packets)
            - Disk I/O (read/write bytes, IOPS)
K3s API → kube-state-metrics (8080) → Prometheus
Demo App → /metrics endpoint (3000) → Prometheus
```

**Cost Optimization**:

- Run Prometheus on master node (no extra EC2 cost)
- Use local storage (20GB EBS already allocated)
- No Prometheus Operator (reduces complexity, saves 100MB RAM)
- Retention: 7 days (vs. 15 days default) → saves 50% storage

**Scalability Strategy**:

- **Current setup**: Single Prometheus instance (sufficient for 10 nodes)
- **Scale beyond 10 nodes**: Add Prometheus federation (1 master, N shards)
- **Scale beyond 50 nodes**: Migrate to Thanos (long-term storage in S3, cheaper)

**Implementation Steps**:

```bash
# 1. Deploy Prometheus as StatefulSet (persistent storage)
kubectl create namespace monitoring
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus prometheus-community/prometheus \
  --namespace monitoring \
  --set server.retention=7d \
  --set server.persistentVolume.size=20Gi \
  --set server.service.type=NodePort \
  --set server.service.nodePort=30090

# 2. Deploy kube-state-metrics (lightweight, no persistence)
kubectl apply -f https://github.com/kubernetes/kube-state-metrics/releases/download/v2.10.0/kube-state-metrics-standard.yaml

# 3. Verify metrics collection
curl http://<master-ip>:30090/api/v1/query?query=up
```

**Security** (NodePort exposure):

- Security Group: Allow 30090 only from Lambda SG
- No Basic Auth initially (Lambda in same VPC, trusted network)
- **[BONUS]** Add OAuth2 Proxy for external access

**Monitoring the Monitor**:

- CloudWatch alarm if Prometheus pod crashes
- Self-monitoring: Prometheus scrapes its own metrics
- Alert if scrape failures > 5% (indicates node issues)

---

## 2. Smart Scaling Logic (Lambda Function)

### Solution Design

**Lambda Function Architecture**:

```python
# autoscaler.py (main handler)
def lambda_handler(event, context):
    try:
        # 1. Pre-flight checks (10s)
        state = get_cluster_state()  # DynamoDB read
        if state['scaling_in_progress']:
            logger.info("Scaling in progress, exiting")
            return
        if not check_cooldown(state['last_scale_time']):
            logger.info("Within cooldown period, exiting")
            return

        # 2. Collect metrics (15s)
        metrics = collect_metrics()  # Prometheus queries

        # 3. Make decision (5s)
        decision = evaluate_scaling_policy(metrics, state)

        # 4. Execute scaling (25s)
        if decision['action'] == 'scale_up':
            acquire_lock()
            scale_up(decision['count'])
        elif decision['action'] == 'scale_down':
            acquire_lock()
            scale_down(decision['node_to_remove'])
        else:
            logger.info("No action needed")

        # 5. Update state & notify (5s)
        release_lock()
        send_notification(decision)

    except Exception as e:
        logger.error(f"Autoscaler failed: {e}")
        release_lock()  # Always release lock
        send_alert(f"🔴 Scaling error: {e}")
        raise  # Let Lambda retry
```

**Cost Optimization Strategies**:

1. **Dynamic Invocation Frequency** (saves 40% Lambda costs):

```python
# EventBridge dynamic scheduling
def adjust_schedule_based_on_time():
    current_hour = datetime.now().hour
    if 9 <= current_hour <= 21:  # Peak hours
        return "rate(2 minutes)"  # $0.40/month
    else:  # Off-peak
        return "rate(5 minutes)"  # $0.24/month
```

2. **Metric Caching** (reduce Prometheus load):

```python
# Cache recent metrics in DynamoDB (avoid repeated queries)
cached_metrics = get_cached_metrics()  # TTL: 1 minute
if cached_metrics and not stale(cached_metrics):
    return cached_metrics
else:
    fresh_metrics = query_prometheus()
    cache_metrics(fresh_metrics, ttl=60)
    return fresh_metrics
```

3. **Intelligent Scale-Up Sizing** (avoid over-provisioning):

```python
def calculate_scale_up_count(metrics):
    cpu = metrics['cpu_percent']
    pending_pods = metrics['pending_pods']

    # Aggressive: High urgency
    if cpu > 85 or pending_pods > 10:
        return 2  # Add 2 nodes

    # Conservative: Moderate load
    elif cpu > 70 or pending_pods > 3:
        return 1  # Add 1 node

    else:
        return 0  # No action
```

**Scalability Strategy**:

- Lambda auto-scales (no config needed)
- If processing time > 60s, split into Step Functions workflow
- Use Lambda reserved concurrency (1) to prevent duplicate executions

**Error Handling**:

```python
class AutoscalerError(Exception): pass
class PrometheusUnavailableError(AutoscalerError): pass
class EC2QuotaExceededError(AutoscalerError): pass

# Retry logic with exponential backoff
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(PrometheusUnavailableError)
)
def query_prometheus():
    response = requests.get(PROMETHEUS_URL, timeout=10)
    if response.status_code != 200:
        raise PrometheusUnavailableError()
    return response.json()
```

---

## 3. Automated Node Provisioning

### Solution Design

**EC2 Launch Template Strategy**:

```typescript
// Pulumi TypeScript
const workerLaunchTemplate = new aws.ec2.LaunchTemplate("k3s-worker", {
  imageId: "ami-0c55b159cbfafe1f0", // Amazon Linux 2023 (free tier eligible)
  instanceType: "t3.small", // $0.0208/hour = $15/month

  // Cost optimization: Use gp3 instead of gp2 (20% cheaper)
  blockDeviceMappings: [
    {
      deviceName: "/dev/xvda",
      ebs: {
        volumeSize: 20,
        volumeType: "gp3", // $0.08/GB-month vs. $0.10 for gp2
        iops: 3000, // Free baseline
        throughput: 125, // Free baseline
        encrypted: true,
      },
    },
  ],

  // User Data: Auto-join K3s cluster
  userData: pulumi.output(masterInstance.privateIp).apply((masterIp) =>
    Buffer.from(
      `#!/bin/bash
set -e

# Install K3s agent
export K3S_URL="https://${masterIp}:6443"
export K3S_TOKEN=$(aws secretsmanager get-secret-value \
    --secret-id ${k3sTokenSecret.name} \
    --query SecretString \
    --output text \
    --region us-east-1)

curl -sfL https://get.k3s.io | sh -s - agent

# Wait for node to be ready
for i in {1..30}; do
    if systemctl is-active --quiet k3s-agent; then
        echo "K3s agent started successfully"
        break
    fi
    sleep 10
done

# Install CloudWatch agent for logs
wget https://s3.amazonaws.com/amazoncloudwatch-agent/amazon_linux/amd64/latest/amazon-cloudwatch-agent.rpm
rpm -U ./amazon-cloudwatch-agent.rpm
        `
    ).toString("base64")
  ),
});
```

**Cost Optimization - Multi-AZ Strategy**:

```python
# Distribute nodes across 2 AZs (not 3) to save NAT Gateway costs
# 2 NAT Gateways ($32/month) vs. 3 NAT Gateways ($48/month)

def select_subnet_for_new_node():
    nodes_by_az = count_nodes_per_availability_zone()

    # Prefer AZ with fewer nodes
    if nodes_by_az['us-east-1a'] <= nodes_by_az['us-east-1b']:
        return subnet_az_a
    else:
        return subnet_az_b
```

**Faster Node Join** (reduce from 5 min → 2 min):

1. **Use Amazon Linux 2023** (optimized boot time: 20s vs. 45s for Ubuntu)
2. **Pre-baked AMI** (optional): Install K3s in AMI, only retrieve token at boot
3. **Parallel operations**: Don't wait for `systemctl status`, check from Lambda

**Scalability**:

- Launch template supports unlimited concurrent launches
- AWS default limit: 20 On-Demand instances per region (request increase if needed)
- Use EC2 Fleet API for launching 5+ instances simultaneously

---

## 4. Graceful Node Deprovisioning

### Solution Design

**Safe Scale-Down Algorithm**:

```python
def select_node_to_remove():
    nodes = get_all_worker_nodes()

    # Filter out unsafe nodes
    safe_candidates = []
    for node in nodes:
        pods = get_pods_on_node(node)

        # Skip if critical system pods
        if has_system_pods(pods):
            continue

        # Skip if StatefulSet pods (data loss risk)
        if has_stateful_pods(pods):
            continue

        # Skip if single-replica deployments
        if has_single_replica_pods(pods):
            continue

        safe_candidates.append({
            'node': node,
            'pod_count': len(pods),
            'cpu_utilization': get_node_cpu(node),
            'uptime': get_node_uptime(node)
        })

    # Select node with lowest pod count, then lowest CPU
    if not safe_candidates:
        raise NoSafeNodeToRemoveError("All nodes have critical workloads")

    return sorted(safe_candidates, key=lambda x: (x['pod_count'], x['cpu_utilization']))[0]
```

**Drain Process with Timeout**:

```python
def drain_node_safely(node_name, timeout=300):
    start_time = time.time()

    # Step 1: Cordon (prevent new pods)
    subprocess.run(['kubectl', 'cordon', node_name], check=True)

    # Step 2: Drain with grace period
    try:
        subprocess.run([
            'kubectl', 'drain', node_name,
            '--ignore-daemonsets',
            '--delete-emptydir-data',
            '--force',  # Delete pods not managed by ReplicationController/ReplicaSet
            f'--timeout={timeout}s',
            '--grace-period=60'  # Allow 60s for graceful shutdown
        ], check=True, timeout=timeout)

    except subprocess.TimeoutExpired:
        logger.error(f"Drain timeout for {node_name}, aborting scale-down")
        # Uncordon node (restore to normal)
        subprocess.run(['kubectl', 'uncordon', node_name])
        raise DrainTimeoutError()

    # Step 3: Verify no pods remain (except DaemonSets)
    remaining_pods = get_non_daemonset_pods(node_name)
    if remaining_pods:
        logger.error(f"Pods still running on {node_name}: {remaining_pods}")
        subprocess.run(['kubectl', 'uncordon', node_name])
        raise DrainFailedError()

    # Step 4: Delete node from cluster
    subprocess.run(['kubectl', 'delete', 'node', node_name], check=True)

    # Step 5: Terminate EC2 instance
    instance_id = get_instance_id_from_node_name(node_name)
    ec2.terminate_instances(InstanceIds=[instance_id])

    logger.info(f"Successfully removed node {node_name} in {time.time() - start_time:.1f}s")
```

**Cost Optimization**:

- Always scale down to minimum 2 nodes during off-peak (save $30-45/month)
- Prefer removing newest nodes first (less accumulated state, faster drain)
- Never terminate instances in the middle of the hour (AWS bills hourly)

**Scalability**:

- Never remove more than 1 node per invocation (prevent cascading failures)
- Cooldown: 10 minutes after scale-down (allow metrics to stabilize)

---

## 5. State Management & Race Condition Prevention

### Solution Design

**DynamoDB Table Schema** (optimized for conditional writes):

```python
# Primary Key: cluster_id (partition key only, no sort key needed)
{
    'cluster_id': 'smartscale-prod',  # PK
    'node_count': 5,  # Current worker count
    'last_scale_time': 1703251200,  # Unix timestamp
    'last_scale_action': 'scale_up',  # 'scale_up' | 'scale_down' | 'none'
    'last_scale_reason': 'CPU 78%, Pending pods: 5',
    'scaling_in_progress': 'lambda-xyz-20251222103045',  # Lock holder ID
    'lock_expiry': 1703251500,  # Unix timestamp (5 min from acquisition)
    'lock_acquired_at': 1703251200,
    'version': 42  # Optimistic locking counter
}
```

**Lock Acquisition Pattern** (prevents race conditions):

```python
import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('k3s-autoscaler-state')

def acquire_lock(cluster_id, lambda_request_id):
    current_time = int(time.time())
    lock_expiry = current_time + 300  # 5 minutes

    try:
        # Conditional write: only succeed if no active lock OR lock expired
        table.put_item(
            Item={
                'cluster_id': cluster_id,
                'scaling_in_progress': lambda_request_id,
                'lock_expiry': lock_expiry,
                'lock_acquired_at': current_time
            },
            ConditionExpression='attribute_not_exists(scaling_in_progress) OR lock_expiry < :now',
            ExpressionAttributeValues={':now': current_time}
        )
        logger.info(f"Lock acquired by {lambda_request_id}")
        return True

    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            logger.warning("Lock already held, exiting gracefully")
            return False
        raise

def release_lock(cluster_id, lambda_request_id):
    # Only release if this Lambda holds the lock (prevent accidental release)
    try:
        table.update_item(
            Key={'cluster_id': cluster_id},
            UpdateExpression='REMOVE scaling_in_progress, lock_expiry, lock_acquired_at',
            ConditionExpression='scaling_in_progress = :request_id',
            ExpressionAttributeValues={':request_id': lambda_request_id}
        )
        logger.info(f"Lock released by {lambda_request_id}")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            logger.warning("Lock was already released or taken by another process")
```

**Cost Optimization**:

- **On-Demand pricing**: $1.25 per million writes (perfect for low traffic)
  - Expected: ~15,000 writes/month = $0.02/month
- **No provisioned capacity**: Don't pay for unused throughput
- **TTL attribute**: Auto-delete old records after 30 days (no manual cleanup)

**Handling Lambda Timeouts**:

```python
def cleanup_stale_locks():
    """Run at start of each invocation"""
    current_time = int(time.time())
    state = table.get_item(Key={'cluster_id': CLUSTER_ID}).get('Item')

    if state and 'lock_expiry' in state:
        if state['lock_expiry'] < current_time:
            logger.warning(f"Stale lock detected, cleaning up")
            release_lock(CLUSTER_ID, state['scaling_in_progress'])
```

**Scalability**:

- DynamoDB handles millions of requests/second (no limits for this use case)
- If managing 100+ clusters, use cluster_id as partition key (good distribution)

---

## 6. Monitoring, Logging & Alerting

### Solution Design

**Cost-Optimized Monitoring Stack**:

**Tier 1: Free Prometheus** (K8s metrics):

- Self-hosted on master node: $0
- 15-second granularity
- 7-day retention

**Tier 2: CloudWatch** (Lambda + EC2 metrics):

- Lambda logs: 5GB/month = $2.50
- Custom metrics: 10 metrics × $0.30 = $3.00
- Alarms: 10 alarms × $0.10 = $1.00
- **Total**: $6.50/month

**Tier 3: SNS Notifications** (Slack + Email):

- 1,000 notifications/month: $0.50
- Email: Free
- SMS: $0.00645/message (only for critical)

**Grafana Dashboard Strategy**:

```bash
# Option 1: Run on master node (FREE)
kubectl apply -f grafana-deployment.yaml
# Access: http://master-ip:32000

# Option 2: Grafana Cloud (FREE tier)
# - 10k series, 50GB logs, 14-day retention
# - No infrastructure cost
# - Better for production (HA, managed)
```

**CloudWatch Custom Metrics** (publish from Lambda):

```python
import boto3
cloudwatch = boto3.client('cloudwatch')

def publish_metrics(decision, metrics):
    cloudwatch.put_metric_data(
        Namespace='SmartScale/Autoscaler',
        MetricData=[
            {
                'MetricName': 'CurrentNodeCount',
                'Value': metrics['node_count'],
                'Unit': 'Count',
                'Timestamp': datetime.utcnow()
            },
            {
                'MetricName': 'ClusterCPUUtilization',
                'Value': metrics['cpu_percent'],
                'Unit': 'Percent'
            },
            {
                'MetricName': 'PendingPods',
                'Value': metrics['pending_pods'],
                'Unit': 'Count'
            },
            {
                'MetricName': 'ScalingDecision',
                'Value': 1 if decision['action'] == 'scale_up' else 0,
                'Unit': 'None',
                'Dimensions': [{'Name': 'Action', 'Value': decision['action']}]
            }
        ]
    )
```

**Slack Notification Optimization** (reduce noise):

```python
# Only send notifications for:
# 1. Scale-up/down events (actual changes)
# 2. Failures (errors)
# 3. Warnings (approaching limits)

# DON'T spam Slack with "no action needed" messages

def should_notify(decision):
    return decision['action'] in ['scale_up', 'scale_down', 'error', 'warning']

def send_slack_notification(decision):
    if not should_notify(decision):
        return

    webhook_url = get_secret('slack-webhook-url')
    emoji = {
        'scale_up': '🟢',
        'scale_down': '🔵',
        'error': '🔴',
        'warning': '⚠️'
    }.get(decision['action'], 'ℹ️')

    message = {
        "text": f"{emoji} SmartScale Event",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Action:* {decision['action']}\n*Reason:* {decision['reason']}\n*New Node Count:* {decision['new_count']}"
                }
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Cluster: {CLUSTER_ID} | Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    }
                ]
            }
        ]
    }

    requests.post(webhook_url, json=message)
```

---

## 7. Security & Compliance

### Solution Design

**Secrets Management Strategy**:

```typescript
// Store K3s token in Secrets Manager (NOT Parameter Store)
const k3sToken = new aws.secretsmanager.Secret("k3s-token", {
  description: "K3s cluster join token",
  recoveryWindowInDays: 7, // Don't delete immediately (accident recovery)
});

new aws.secretsmanager.SecretVersion("k3s-token-value", {
  secretId: k3sToken.id,
  secretString: k3sTokenValue, // Generated during master setup
});

// Rotate token every 90 days (security best practice)
new aws.secretsmanager.SecretRotation("k3s-token-rotation", {
  secretId: k3sToken.id,
  rotationLambdaArn: tokenRotationLambda.arn,
  rotationRules: {
    automaticallyAfterDays: 90,
  },
});
```

**IAM Least-Privilege Pattern**:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EC2ScalingOnly",
      "Effect": "Allow",
      "Action": ["ec2:RunInstances", "ec2:TerminateInstances"],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "ec2:InstanceType": ["t3.small", "t3.medium"],
          "aws:RequestedRegion": "us-east-1"
        },
        "StringLike": {
          "ec2:LaunchTemplate": "arn:aws:ec2:us-east-1:*:launch-template/lt-k3s-worker-*"
        }
      }
    },
    {
      "Sid": "DynamoDBStateTableOnly",
      "Effect": "Allow",
      "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"],
      "Resource": "arn:aws:dynamodb:us-east-1:*:table/k3s-autoscaler-state"
    },
    {
      "Sid": "SecretsManagerReadOnly",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": [
        "arn:aws:secretsmanager:us-east-1:*:secret:k3s-token-*",
        "arn:aws:secretsmanager:us-east-1:*:secret:slack-webhook-*"
      ]
    }
  ]
}
```

**Network Security - Defense in Depth**:

```
Layer 1: VPC Isolation
  - Private subnets for workers (no public IPs)
  - NAT Gateway for outbound only

Layer 2: Security Groups (Stateful Firewall)
  - Master SG: Allow 6443 from workers only
  - Worker SG: Allow all from master, block all inbound
  - Lambda SG: Allow outbound to master:30090 only
  - Prometheus SG: Allow 30090 from Lambda SG only

Layer 3: K3s Network Policies
  - Deny all traffic by default
  - Whitelist demo-app → internet
  - Whitelist monitoring → all namespaces

Layer 4: Application-Level Auth
  - Prometheus: Basic auth (optional)
  - Grafana: OAuth2 with Google/GitHub
```

**Encryption Everywhere**:

- **In Transit**: TLS 1.3 for all K3s communication (default)
- **At Rest**: EBS volumes encrypted with AWS-managed KMS keys
- **Secrets**: Secrets Manager uses AES-256
- **DynamoDB**: Server-side encryption enabled

**Cost Impact**: All encryption features are FREE (no additional charges).

---

## 8. Cost Optimization (Advanced Strategies)

### Bonus: Spot Instance Integration

**Cost Savings**: 60-70% off On-Demand pricing ($15/month → $5/month per node)

**Implementation Strategy**:

```python
def launch_instances_with_spot_fallback(count):
    try:
        # First attempt: Spot instances (diversified)
        response = ec2.run_instances(
            MinCount=count,
            MaxCount=count,
            LaunchTemplate={'LaunchTemplateId': worker_template_id},
            InstanceMarketOptions={
                'MarketType': 'spot',
                'SpotOptions': {
                    'MaxPrice': '0.0250',  # 20% above On-Demand ($0.0208)
                    'SpotInstanceType': 'one-time',
                    'InstanceInterruptionBehavior': 'terminate'
                }
            },
            # Diversification: Use multiple instance types
            InstanceType='t3.small',  # Primary
            # Fallback types: t3a.small, t2.small (if t3 unavailable)
        )
        logger.info(f"Launched {count} Spot instances")
        return response

    except ClientError as e:
        if 'InsufficientInstanceCapacity' in str(e):
            logger.warning("Spot unavailable, falling back to On-Demand")
            # Fallback: On-Demand (guaranteed availability)
            return launch_on_demand_instances(count)
        raise
```

**Spot Interruption Handling** (2-minute warning):

```python
# EventBridge rule for EC2 Spot Instance Interruption Notices
{
  "source": ["aws.ec2"],
  "detail-type": ["EC2 Spot Instance Interruption Warning"],
  "detail": {
    "instance-id": [{"exists": true}]
  }
}

# Lambda handler for interruption
def handle_spot_interruption(event):
    instance_id = event['detail']['instance-id']
    node_name = get_node_name_from_instance_id(instance_id)

    logger.warning(f"Spot interruption for {instance_id}, draining immediately")

    # Fast drain (90 seconds, leaves 30s buffer)
    kubectl_cordon(node_name)
    kubectl_drain(node_name, timeout=90, grace_period=30)

    # Launch replacement (prefer Spot again)
    launch_instances_with_spot_fallback(count=1)

    # No need to terminate (AWS does it automatically)
```

**Spot/On-Demand Mix Strategy**:

```python
# Always keep minimum 2 On-Demand nodes (stability)
# Scale additional capacity with Spot (cost savings)

def decide_instance_market_type(current_on_demand_count):
    if current_on_demand_count < 2:
        return 'on-demand'  # Ensure baseline stability
    else:
        return 'spot'  # Additional capacity via Spot
```

**Cost Tracking**:

```python
# Tag instances with market type for cost analysis
tags = [
    {'Key': 'Project', 'Value': 'SmartScale'},
    {'Key': 'InstanceMarket', 'Value': 'spot' if is_spot else 'on-demand'},
    {'Key': 'HourlyRate', 'Value': '0.0062' if is_spot else '0.0208'}
]
```

---

### Bonus: Predictive Scaling

**Goal**: Pre-scale BEFORE traffic spike (reduce latency, improve UX)

**Data Collection** (store historical metrics):

```python
# Every scaling event, record timestamp + metrics to DynamoDB
def record_metrics_history(metrics):
    history_table.put_item(Item={
        'timestamp': int(time.time()),
        'hour_of_day': datetime.now().hour,
        'day_of_week': datetime.now().weekday(),
        'cpu_percent': metrics['cpu_percent'],
        'memory_percent': metrics['memory_percent'],
        'request_rate': metrics['request_rate'],
        'node_count': metrics['node_count'],
        'ttl': int(time.time()) + (7 * 24 * 3600)  # 7-day retention
    })
```

**Prediction Algorithm** (simple moving average):

```python
def predict_future_load(current_time):
    current_hour = current_time.hour
    current_day = current_time.weekday()

    # Query last 7 days at same hour
    response = history_table.query(
        IndexName='hour-day-index',
        KeyConditionExpression='hour_of_day = :hour AND day_of_week = :day',
        ExpressionAttributeValues={
            ':hour': current_hour,
            ':day': current_day
        }
    )

    if len(response['Items']) < 3:
        return None  # Not enough data

    # Calculate average CPU at this time over past 7 days
    avg_cpu = sum(item['cpu_percent'] for item in response['Items']) / len(response['Items'])

    # Predict: If avg_cpu at this hour > 70%, pre-scale 10 min early
    if avg_cpu > 70:
        return 'scale_up_preemptive'
    else:
        return 'no_action'
```

**Execution** (adjust Lambda schedule):

```python
# Run predictive check at :50 minutes (10 min before hour)
# E.g., at 8:50 AM, check if 9 AM historically needs more capacity

def lambda_handler(event, context):
    current_minute = datetime.now().minute

    if current_minute == 50:  # Predictive window
        prediction = predict_future_load(datetime.now() + timedelta(minutes=10))
        if prediction == 'scale_up_preemptive':
            logger.info("Predictive scaling: pre-scaling for anticipated load")
            scale_up(count=1, reason="Predictive (historical pattern)")
    else:
        # Normal reactive scaling
        metrics = collect_metrics()
        decision = evaluate_scaling_policy(metrics)
        execute_decision(decision)
```

**Cost Impact**: None (uses existing Lambda invocations, DynamoDB queries)

**Value**: Prevents customer-facing latency spikes during known peak times (9 AM, flash sales)

---

### Bonus: Custom Application Metrics

**Goal**: Scale based on application-specific signals beyond CPU/memory

**Metrics to Integrate**:

1. **RabbitMQ Queue Depth** (Message Queue Monitoring):

```python
# Prometheus query for RabbitMQ
RABBITMQ_QUEUE_QUERY = 'rabbitmq_queue_messages{queue="orders"}'

def check_queue_depth():
    response = query_prometheus(RABBITMQ_QUEUE_QUERY)
    queue_depth = int(response['data']['result'][0]['value'][1])

    if queue_depth > 1000:
        return 'scale_up', f'Queue depth: {queue_depth} messages'
    elif queue_depth < 100:
        return 'can_scale_down', f'Queue depth: {queue_depth} messages'
    else:
        return 'no_action', f'Queue depth normal: {queue_depth}'
```

2. **API Latency P95** (User Experience Monitoring):

```python
# PromQL for API latency percentile
API_LATENCY_QUERY = '''
histogram_quantile(0.95,
  rate(http_request_duration_seconds_bucket{endpoint="/api/checkout"}[5m])
)
'''

def check_api_latency():
    response = query_prometheus(API_LATENCY_QUERY)
    p95_latency = float(response['data']['result'][0]['value'][1])

    if p95_latency > 2.0:  # 2 seconds
        return 'scale_up', f'API latency p95: {p95_latency:.2f}s'
    else:
        return 'no_action', f'API latency healthy: {p95_latency:.2f}s'
```

3. **Error Rate** (Service Health Monitoring):

```python
# PromQL for error rate calculation
ERROR_RATE_QUERY = '''
sum(rate(http_requests_total{status=~"5.."}[2m])) /
sum(rate(http_requests_total[2m])) * 100
'''

def check_error_rate():
    response = query_prometheus(ERROR_RATE_QUERY)
    error_rate = float(response['data']['result'][0]['value'][1])

    if error_rate > 5.0:  # 5% error rate
        return 'scale_up_urgent', f'Error rate: {error_rate:.1f}%'
    else:
        return 'no_action', f'Error rate normal: {error_rate:.1f}%'
```

**Integrated Decision Logic**:

```python
def evaluate_scaling_policy_with_app_metrics(metrics, state):
    reasons = []
    urgency = 0

    # Traditional metrics
    if metrics['cpu_percent'] > 70:
        reasons.append(f"CPU {metrics['cpu_percent']:.1f}%")
        urgency = max(urgency, 1)

    if metrics['pending_pods'] > 3:
        reasons.append(f"Pending pods: {metrics['pending_pods']}")
        urgency = max(urgency, 2)

    # Custom app metrics
    queue_action, queue_msg = check_queue_depth()
    if queue_action == 'scale_up':
        reasons.append(queue_msg)
        urgency = max(urgency, 1)

    latency_action, latency_msg = check_api_latency()
    if latency_action == 'scale_up':
        reasons.append(latency_msg)
        urgency = max(urgency, 2)

    error_action, error_msg = check_error_rate()
    if error_action == 'scale_up_urgent':
        reasons.append(error_msg)
        urgency = 3  # Highest priority

    # Decide action based on urgency
    if urgency >= 3:
        return {'action': 'scale_up', 'count': 2, 'reason': ', '.join(reasons)}
    elif urgency >= 1:
        return {'action': 'scale_up', 'count': 1, 'reason': ', '.join(reasons)}
    elif all([
        metrics['cpu_percent'] < 30,
        metrics['pending_pods'] == 0,
        queue_action == 'can_scale_down'
    ]):
        return {'action': 'scale_down', 'count': 1, 'reason': 'Low utilization'}
    else:
        return {'action': 'no_action', 'reason': 'Within thresholds'}
```

**Cost Impact**: Zero (uses existing Prometheus infrastructure)

**Value**:

- Prevents cascading failures (scale before 5xx errors spike)
- Better user experience (scale before latency degrades)
- Business-aware scaling (queue backlog = delayed orders)

---

### Bonus: GitOps Configuration Management

**Goal**: Version-control all K8s configs, automated deployments, audit trail

**Solution: FluxCD** (lightweight, GitOps-native)

**Architecture**:

```
Git Repo (k8s/ folder) → FluxCD watches → Auto-apply to K3s cluster
    ↓
Commit → CI/CD validation → Merge → FluxCD syncs → Deployed
```

**Implementation Steps**:

1. **Install FluxCD on K3s**:

```bash
# Install Flux CLI
curl -s https://fluxcd.io/install.sh | bash

# Bootstrap Flux to Git repo
flux bootstrap github \
  --owner=BayajidAlam \
  --repository=node-fleet \
  --path=k8s/clusters/production \
  --personal \
  --private=false
```

2. **Project Structure**:

```
k8s/
├── clusters/
│   └── production/
│       ├── flux-system/        # FluxCD configs (auto-generated)
│       ├── infrastructure/
│       │   ├── prometheus.yaml
│       │   ├── kube-state-metrics.yaml
│       │   └── grafana.yaml
│       └── apps/
│           └── demo-app/
│               ├── deployment.yaml
│               ├── service.yaml
│               └── hpa.yaml (if using HPA)
```

3. **GitOps Workflow**:

```bash
# Developer makes change
vim k8s/apps/demo-app/deployment.yaml
# Change replicas: 5 → 10

git add k8s/apps/demo-app/deployment.yaml
git commit -m "Scale demo-app to 10 replicas"
git push origin main

# FluxCD detects change within 1 minute
# Auto-applies to cluster
# No kubectl needed!
```

4. **Rollback with Git Revert**:

```bash
# Oops, deployment failed
git log --oneline
# abc123 Scale demo-app to 10 replicas
# def456 Add health check endpoint

git revert abc123
git push origin main

# FluxCD auto-reverts in cluster
# Cluster state = previous working version
```

5. **Monitoring FluxCD**:

```bash
# Check Flux status
flux get all

# View reconciliation logs
kubectl logs -n flux-system deploy/source-controller

# Trigger manual sync
flux reconcile source git flux-system
```

**Benefits**:

- **Audit Trail**: Every change tracked in Git history
- **Rollback**: `git revert` to undo bad deployments
- **CI/CD Integration**: GitHub Actions validate YAML before merge
- **No kubectl Access**: Developers commit to Git, no cluster credentials needed
- **Drift Detection**: FluxCD reverts manual `kubectl` changes

**Cost Impact**: Zero (FluxCD runs as pods on existing cluster)

**Security**:

- FluxCD uses SSH keys for Git authentication (stored in K8s secrets)
- No AWS credentials in Git repo (use IAM roles)

---

### Bonus: Real-Time Cost Dashboard

**Goal**: Visualize spending, track savings, compare to budget in real-time

**Solution: Grafana Panel with Cost Calculations**

**Data Sources**:

1. **CloudWatch Custom Metrics** (from Lambda):

```python
def publish_cost_metrics():
    nodes = get_all_worker_nodes()
    cost_breakdown = calculate_hourly_cost(nodes)

    cloudwatch.put_metric_data(
        Namespace='SmartScale/Cost',
        MetricData=[
            {
                'MetricName': 'HourlyCost',
                'Value': cost_breakdown['total_hourly'],
                'Unit': 'None',
                'Dimensions': [{'Name': 'Currency', 'Value': 'BDT'}]
            },
            {
                'MetricName': 'SpotInstanceCount',
                'Value': cost_breakdown['spot_count'],
                'Unit': 'Count'
            },
            {
                'MetricName': 'OnDemandInstanceCount',
                'Value': cost_breakdown['on_demand_count'],
                'Unit': 'Count'
            },
            {
                'MetricName': 'EstimatedMonthlyCost',
                'Value': cost_breakdown['total_hourly'] * 730,  # hours/month
                'Unit': 'None'
            }
        ]
    )

def calculate_hourly_cost(nodes):
    spot_count = 0
    on_demand_count = 0

    for node in nodes:
        tags = ec2.describe_tags(Filters=[
            {'Name': 'resource-id', 'Values': [node['instance_id']]}
        ])

        market_type = next((t['Value'] for t in tags['Tags'] if t['Key'] == 'InstanceMarket'), 'on-demand')

        if market_type == 'spot':
            spot_count += 1
        else:
            on_demand_count += 1

    # Pricing (BDT)
    spot_hourly = 0.0062 * 120  # $0.0062 * 120 BDT/USD ≈ 0.74 BDT/hour
    on_demand_hourly = 0.0208 * 120  # $0.0208 * 120 ≈ 2.50 BDT/hour

    total_hourly = (spot_count * spot_hourly) + (on_demand_count * on_demand_hourly)

    return {
        'spot_count': spot_count,
        'on_demand_count': on_demand_count,
        'total_hourly': total_hourly,
        'spot_hourly': spot_hourly * spot_count,
        'on_demand_hourly': on_demand_hourly * on_demand_count
    }
```

2. **Grafana Dashboard Panels**:

**Panel 1: Current Daily Cost (Gauge)**

```
Query: sum(SmartScale_Cost_HourlyCost) * 24
Thresholds:
  - Green: < 1500 BDT/day
  - Yellow: 1500-2000 BDT/day
  - Red: > 2000 BDT/day
Target: 1200 BDT/day (50% savings from 2400 BDT baseline)
```

**Panel 2: Monthly Cost Projection (Single Stat)**

```
Query: sum(SmartScale_Cost_EstimatedMonthlyCost)
Display: "60,500 BDT" with trend arrow (↓ 49% vs baseline)
```

**Panel 3: Cost Over Time (Graph)**

```
Query 1 (Actual): sum(SmartScale_Cost_HourlyCost) * 24
Query 2 (Baseline): 120000 / 30 (constant line at 4000 BDT/day)
Time Range: Last 30 days
Fill Area: Show savings (gap between baseline and actual)
```

**Panel 4: Cost Breakdown (Pie Chart)**

```
Queries:
  - Spot Instances: sum(SmartScale_Cost_SpotInstanceCount) * 0.74
  - On-Demand: sum(SmartScale_Cost_OnDemandInstanceCount) * 2.50
  - Lambda: 500 BDT/month (static)
  - DynamoDB: 200 BDT/month (static)
  - CloudWatch: 500 BDT/month (static)
```

**Panel 5: Savings Meter (Bar Gauge)**

```
Query: (120000 - sum(SmartScale_Cost_EstimatedMonthlyCost)) / 120000 * 100
Display: "49% Savings" (59,500 BDT saved)
Color: Gradient green (higher = better)
```

**Panel 6: Spot vs On-Demand Ratio (Stat)**

```
Query: sum(SmartScale_Cost_SpotInstanceCount) /
       (sum(SmartScale_Cost_SpotInstanceCount) + sum(SmartScale_Cost_OnDemandInstanceCount)) * 100
Display: "67% Spot Utilization"
Target: >60% (maximize savings)
```

**Panel 7: Instance Hours Trend (Stacked Graph)**

```
Query 1: sum(SmartScale_Cost_SpotInstanceCount)
Query 2: sum(SmartScale_Cost_OnDemandInstanceCount)
Stack: Yes
Legend: Show hourly cost per instance type
```

**Weekly Cost Report (Automated Email)**:

```python
def generate_weekly_cost_report():
    """Run every Monday 9 AM via EventBridge"""
    week_data = cloudwatch.get_metric_statistics(
        Namespace='SmartScale/Cost',
        MetricName='EstimatedMonthlyCost',
        StartTime=datetime.now() - timedelta(days=7),
        EndTime=datetime.now(),
        Period=3600,  # 1 hour
        Statistics=['Average']
    )

    avg_monthly_cost = sum(d['Average'] for d in week_data['Datapoints']) / len(week_data['Datapoints'])
    baseline_cost = 120000
    savings = baseline_cost - avg_monthly_cost
    savings_percent = (savings / baseline_cost) * 100

    report = f"""
    📊 SmartScale Weekly Cost Report

    Period: {datetime.now() - timedelta(days=7)} to {datetime.now()}

    💰 Projected Monthly Cost: {avg_monthly_cost:,.0f} BDT
    📉 Savings vs Baseline: {savings:,.0f} BDT ({savings_percent:.1f}%)

    🟢 Spot Instance Usage: {get_spot_percentage():.0f}%
    ⚡ Avg Nodes Running: {get_avg_node_count():.1f}

    Next Steps:
    - Current trajectory: On track for 50% cost reduction
    - Consider increasing Spot ratio to 80% for additional savings

    View Dashboard: http://grafana.smartscale.local/d/cost-dashboard
    """

    send_email(to='devops@techflow.com', subject='SmartScale Cost Report', body=report)
    send_slack_notification(report)
```

**Cost Optimization Alerts**:

```python
# CloudWatch Alarm: Cost exceeds budget
cloudwatch.put_metric_alarm(
    AlarmName='SmartScale-CostOverBudget',
    MetricName='EstimatedMonthlyCost',
    Namespace='SmartScale/Cost',
    Statistic='Average',
    Period=3600,
    EvaluationPeriods=6,  # 6 hours
    Threshold=75000,  # 75k BDT (budget threshold)
    ComparisonOperator='GreaterThanThreshold',
    AlarmActions=[sns_topic_arn],
    AlarmDescription='Monthly cost projection exceeds 75k BDT budget'
)

# CloudWatch Alarm: Low Spot utilization
cloudwatch.put_metric_alarm(
    AlarmName='SmartScale-LowSpotUsage',
    MetricName='SpotInstanceCount',
    Namespace='SmartScale/Cost',
    Statistic='Average',
    Period=3600,
    EvaluationPeriods=12,  # 12 hours
    Threshold=2,  # Less than 2 Spot instances
    ComparisonOperator='LessThanThreshold',
    AlarmActions=[sns_topic_arn],
    AlarmDescription='Spot instance utilization too low, missing cost savings'
)
```

**Cost Impact**: $3-5/month for additional CloudWatch metrics

**Value**:

- **Visibility**: Real-time cost tracking (know spending every hour)
- **Accountability**: Track savings vs baseline (prove 40-50% reduction)
- **Alerts**: Get notified if costs drift from budget
- **Executive Reporting**: Weekly summaries for leadership

---

## 9. Testing Strategy (Comprehensive)

### Load Testing with k6

**Test Scenario Design**:

```javascript
// tests/load-test.js
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate } from "k6/metrics";

const errorRate = new Rate("errors");

export let options = {
  scenarios: {
    // Scenario 1: Gradual Ramp (simulate normal business hours)
    gradual_ramp: {
      executor: "ramping-vus",
      startVUs: 10,
      stages: [
        { duration: "5m", target: 50 }, // Morning traffic
        { duration: "10m", target: 100 }, // Mid-day peak
        { duration: "5m", target: 50 }, // Afternoon dip
      ],
    },

    // Scenario 2: Flash Sale Spike (sudden traffic surge)
    flash_sale: {
      executor: "ramping-vus",
      startTime: "25m", // Start after gradual ramp
      stages: [
        { duration: "30s", target: 500 }, // Sudden spike
        { duration: "5m", target: 500 }, // Sustained load
        { duration: "2m", target: 100 }, // Cool down
      ],
    },
  },

  thresholds: {
    http_req_duration: ["p(95)<2000"], // 95% under 2s
    http_req_failed: ["rate<0.05"], // <5% error rate
    errors: ["rate<0.05"],
  },
};

export default function () {
  const baseUrl = __ENV.DEMO_APP_URL || "http://master-ip:30080";

  // Mix of endpoints (simulate realistic traffic)
  const endpoints = [
    "/api/products",
    "/api/products/123",
    "/api/cart",
    "/api/checkout",
  ];

  const endpoint = endpoints[Math.floor(Math.random() * endpoints.length)];
  const res = http.get(`${baseUrl}${endpoint}`);

  const success = check(res, {
    "status is 200": (r) => r.status === 200,
    "response time < 2s": (r) => r.timings.duration < 2000,
  });

  errorRate.add(!success);
  sleep(Math.random() * 3 + 1); // 1-4 second think time
}
```

**Expected Autoscaler Behavior**:

```
Time    | VUs  | Expected Nodes | Trigger
--------|------|----------------|---------------------------
0-5m    | 10   | 2 (baseline)   | Low CPU (~20%)
5-10m   | 50   | 2              | CPU ~40% (below threshold)
10-15m  | 100  | 3-4            | CPU >70% → scale-up
15-20m  | 100  | 4              | Stabilized
25m     | 500  | 6-8            | Flash sale spike
30m     | 100  | 6              | Cooldown (5 min wait)
35m     | 100  | 4              | CPU <30% → scale-down
```

**Validation Script**:

```bash
#!/bin/bash
# tests/validate-autoscaling.sh

echo "Starting k6 load test..."
k6 run tests/load-test.js --out json=test-results.json &
K6_PID=$!

echo "Monitoring autoscaler behavior..."
for i in {1..60}; do
  NODES=$(kubectl get nodes --no-headers | wc -l)
  CPU=$(kubectl top nodes | awk '{sum+=$3} END {print sum/NR}')
  PODS=$(kubectl get pods --field-selector=status.phase=Pending --no-headers | wc -l)

  echo "$(date +'%H:%M:%S') | Nodes: $NODES | CPU: ${CPU}% | Pending: $PODS"
  sleep 30
done

wait $K6_PID
echo "Load test complete. Check CloudWatch for scaling events."
```

---

## 10. Deployment Workflow (Pulumi)

### Step-by-Step Deployment

**Phase 1: Infrastructure Setup** (10 minutes)

```bash
cd pulumi
pulumi stack init smartscale-prod
pulumi config set aws:region us-east-1
pulumi config set --secret k3s-token $(openssl rand -base64 32)

# Deploy VPC, Security Groups, IAM roles
pulumi up --yes

# Export master IP
export MASTER_IP=$(pulumi stack output masterIp)
```

**Phase 2: K3s Master Setup** (5 minutes)

```bash
# SSH to master node
ssh -i ~/.ssh/k3s-keypair.pem ec2-user@$(pulumi stack output masterPublicIp)

# Install K3s on master
curl -sfL https://get.k3s.io | sh -s - server \
  --disable traefik \
  --write-kubeconfig-mode 644

# Get join token
sudo cat /var/lib/rancher/k3s/server/node-token

# Store token in Secrets Manager (from local machine)
aws secretsmanager update-secret \
  --secret-id k3s-token \
  --secret-string "$(ssh ec2-user@$MASTER_IP 'sudo cat /var/lib/rancher/k3s/server/node-token')"
```

**Phase 3: Monitoring Stack** (10 minutes)

```bash
# Copy kubeconfig from master
scp ec2-user@$MASTER_IP:/etc/rancher/k3s/k3s.yaml ~/.kube/config
sed -i "s/127.0.0.1/$MASTER_IP/" ~/.kube/config

# Deploy Prometheus
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus prometheus-community/prometheus \
  --namespace monitoring --create-namespace \
  --set server.service.type=NodePort \
  --set server.service.nodePort=30090

# Deploy kube-state-metrics
kubectl apply -f https://github.com/kubernetes/kube-state-metrics/releases/download/v2.10.0/kube-state-metrics-standard.yaml

# Verify metrics
curl http://$MASTER_IP:30090/api/v1/query?query=up
```

**Phase 4: Demo App Deployment** (5 minutes)

```bash
# Build and push to ECR
ECR_URL=$(pulumi stack output ecrRepositoryUrl)
docker build -t demo-app demo-app/
docker tag demo-app:latest $ECR_URL:latest
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_URL
docker push $ECR_URL:latest

# Deploy to K3s
envsubst < k8s/demo-app-deployment.yaml | kubectl apply -f -

# Verify
kubectl get pods -l app=demo-app
curl http://$MASTER_IP:30080/health
```

**Phase 5: Lambda Autoscaler** (5 minutes)

```bash
# Package Lambda
cd lambda
pip install -r requirements.txt -t .
zip -r function.zip .

# Deploy via Pulumi
cd ../pulumi
pulumi up --yes

# Test manual invocation
aws lambda invoke \
  --function-name smartscale-autoscaler \
  --payload '{}' \
  response.json

# Check logs
aws logs tail /aws/lambda/smartscale-autoscaler --follow
```

**Phase 6: Validation** (10 minutes)

```bash
# Trigger scale-up
kubectl run cpu-burn --image=progrium/stress --replicas=10 -- --cpu 2

# Watch nodes scale
watch -n 5 'kubectl get nodes; kubectl top nodes'

# Check Lambda logs for scaling decision
aws logs filter-pattern "scale_up" --log-group-name /aws/lambda/smartscale-autoscaler

# Verify new node joined
kubectl get nodes -o wide
```

**Total Deployment Time**: ~45 minutes from scratch

---

## 🎯 Implementation Priority (4-Week Plan)

### Week 1: Core Infrastructure (Critical Path)

**Goal**: K3s cluster running, manual scaling works

Priority:

1. ✅ VPC, subnets, security groups (Pulumi)
2. ✅ K3s master deployment
3. ✅ Worker launch template + manual join test
4. ✅ Prometheus + kube-state-metrics

**Success Metric**: Can manually launch worker, joins cluster, Prometheus shows metrics

---

### Week 2: Autoscaler Logic (High Value)

**Goal**: Lambda scales nodes automatically

Priority:

1. ✅ DynamoDB state table + lock mechanism
2. ✅ Lambda: Prometheus query + decision logic
3. ✅ Lambda: EC2 launch/terminate
4. ✅ EventBridge trigger (2-min schedule)

**Success Metric**: CPU spike → autoscaler adds node within 3 minutes

---

### Week 3: Monitoring & Safety (Production-Ready)

**Goal**: Observability + graceful scale-down

Priority:

1. ✅ Grafana dashboards (3 dashboards)
2. ✅ CloudWatch alarms + SNS alerts
3. ✅ Slack notifications
4. ✅ Graceful drain logic (safe scale-down)

**Success Metric**: Scale-down removes node without disrupting workloads, alerts sent

---

### Week 4: Optimization & Bonus (Competitive Edge)

**Goal**: Cost savings, advanced features

Priority:

1. ✅ Demo app deployment + k6 load tests
2. ⭐ **[BONUS]** Spot instance integration (60% cost savings)
3. ⭐ **[BONUS]** Predictive scaling (historical patterns)
4. ⭐ **[BONUS]** Multi-AZ node distribution
5. 📄 Documentation + presentation prep

**Success Metric**: Load test proves autoscaling works, cost analysis shows 40-50% savings

---

## 💡 Key Success Factors

### What Will Make This Project Stand Out?

1. **Live Demo Excellence**

   - Real traffic spike → watch nodes scale in real-time
   - Grafana dashboard showing CPU/memory trends
   - Slack notification popping up during demo
   - CloudWatch logs showing decision logic

2. **Cost Analysis Depth**

   - Before/after cost comparison table
   - Monthly savings calculation (60,000 BDT)
   - ROI timeline (payback in 2 months)
   - Spot instance savings visualization

3. **Production-Ready Code**

   - Error handling for ALL edge cases
   - Comprehensive logging (structured JSON)
   - Unit tests with >80% coverage
   - Clear code comments explaining "why"

4. **Operational Maturity**

   - Runbooks for common scenarios
   - Troubleshooting guide with solutions
   - Rollback procedures documented
   - Monitoring alerts tuned (low false positive rate)

5. **Bonus Features Implementation**
   - Spot instances with interruption handling
   - Predictive scaling with historical data
   - Multi-AZ awareness for resilience
   - Custom app metrics (queue depth, latency)

---

## 📊 Final Cost Comparison

| Component                | Before (Manual) | After (Automated) | Savings              |
| ------------------------ | --------------- | ----------------- | -------------------- |
| EC2 (5 nodes, 24/7)      | 120,000 BDT     | -                 | -                    |
| EC2 (2-7 nodes, dynamic) | -               | 60,000 BDT        | 50%                  |
| Lambda                   | -               | 500 BDT           | -                    |
| DynamoDB                 | -               | 200 BDT           | -                    |
| CloudWatch               | -               | 500 BDT           | -                    |
| Secrets Manager          | -               | 200 BDT           | -                    |
| **Total Monthly**        | **120,000 BDT** | **61,400 BDT**    | **58,600 BDT (49%)** |

**Annual Savings**: 702,800 BDT (~$5,870 USD)
**ROI**: Immediate (no upfront investment, only development time)
**Avoided Revenue Loss**: 8 lakh BDT+ (prevented outages during flash sales)

---

This solution balances **cost efficiency, scalability, and operational simplicity**. Every design decision is justified with clear trade-offs, and all bonus features add measurable value without increasing complexity unnecessarily.
