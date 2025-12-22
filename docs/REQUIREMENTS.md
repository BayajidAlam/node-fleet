# SmartScale K3s Autoscaler - Complete Requirements Specification

## 📋 Project Overview

### Business Context

**Client**: TechFlow Solutions - E-commerce startup based in Dhaka, Bangladesh

**Current Situation**:

- **Traffic**: 15,000+ daily users, 80 lakh BDT monthly transactions
- **Infrastructure**: 5 worker nodes running 24/7 on AWS EC2
- **Monthly Cost**: 1.2 lakh BDT (~$1,000 USD)
- **Waste**: 60,000 BDT/month during off-peak hours (50% capacity unused)
- **Incidents**: Last flash sale crashed for 2 hours → 8 lakh BDT lost revenue + 2,000+ complaints
- **Manual Scaling**: 15-20 minutes to add nodes manually via AWS Console

**CTO Mandate**: "Build an intelligent autoscaling system that eliminates waste, prevents outages, and scales automatically – or we move to a managed Kubernetes service at 3x cost."

### Success Criteria

- ✅ **Cost Reduction**: 40-50% infrastructure savings (target: 60,000 BDT/month saved)
- ✅ **Response Time**: Scale from decision to new capacity in < 3 minutes (vs. current 15-20 min)
- ✅ **Reliability**: Zero service disruptions during scaling operations
- ✅ **Automation**: 100% hands-off scaling during traffic spikes
- ✅ **Observability**: Full visibility into scaling decisions and cluster health

---

## 🎯 Core Objectives

### 1. Intelligent Metric Collection & Analysis

- Deploy Prometheus to scrape metrics from all K3s nodes every 15 seconds
- Collect system metrics: CPU, memory, disk I/O, network throughput
- Collect Kubernetes metrics: pod count, pending pods, node conditions
- Collect application metrics: API response latency (p95, p99), error rates, request rate
- Store 7 days of metric history for trend analysis and debugging
- Expose Prometheus API securely for Lambda to query real-time metrics

### 2. Smart Scaling Logic

#### Scale-UP Triggers (ANY condition met):

- Average CPU usage > 70% for 3 consecutive minutes, OR
- Pending pods exist for > 3 minutes (unschedulable workloads), OR
- Memory utilization > 75% cluster-wide, OR
- **[BONUS]** RabbitMQ queue depth > 1000 messages, OR
- **[BONUS]** API response latency p95 > 2 seconds, OR
- **[BONUS]** Error rate > 5% for 2+ minutes

#### Scale-DOWN Triggers (ALL conditions met):

- Average CPU usage < 30% for 10+ consecutive minutes, AND
- No pending pods exist, AND
- Memory utilization < 50% cluster-wide, AND
- **[BONUS]** Queue depth < 100 messages for 10+ minutes

#### Constraints & Parameters:

- **Minimum nodes**: 2 (high availability requirement)
- **Maximum nodes**: 10 (AWS quota + cost limit)
- **Scale-up increment**: Add 1-2 nodes per event (based on urgency)
- **Scale-down increment**: Remove 1 node per event (gradual, safe)
- **Cooldown after scale-up**: 5 minutes (prevent thrashing)
- **Cooldown after scale-down**: 10 minutes (ensure stability)

#### **[BONUS] Predictive Scaling**:

- Analyze last 7 days of traffic patterns
- Pre-scale 10 minutes before known high-traffic periods:
  - Daily 9 AM rush (business hours start)
  - Friday 8 PM flash sales
  - Holiday shopping peaks
- Use time-series forecasting (ARIMA or simple moving average)

### 3. Automated Node Provisioning

**Workflow**:

1. Lambda triggers EC2 RunInstances API with launch template
2. EC2 instance boots with User Data script that:
   - Installs K3s agent via `curl -sfL https://get.k3s.io`
   - Retrieves K3s join token from AWS Secrets Manager
   - Resolves master node IP via EC2 tag query (`Role: k3s-master`)
   - Joins cluster: `K3S_URL=https://<master-ip>:6443 K3S_TOKEN=<token> sh -`
   - Validates join success by checking `systemctl status k3s-agent`
3. Lambda polls node status every 10 seconds (max 5 minutes)
4. Wait for node condition `Ready=True` before marking scale-up complete
5. Tag instance with: `Project: SmartScale`, `ManagedBy: Lambda`, `ScalingGroup: k3s-workers`

**Requirements**:

- Use EC2 Launch Templates for reproducible worker configurations
- User Data script must be idempotent (safe to re-run)
- AMI: Amazon Linux 2023 (AWS-optimized, fast boot times)
- Instance type: t3.small (2 vCPU, 2GB RAM - balances cost/performance)
- Storage: 20GB gp3 EBS volume (encrypted at rest)
- Network: Deploy across 2 availability zones for resilience
- IAM Instance Profile: Allow ECR pulls, Secrets Manager reads, CloudWatch logs writes

### 4. Graceful Node Deprovisioning

**Safety-First Workflow**:

1. **Candidate Selection**: Choose node with:
   - Fewest running pods (minimize disruption)
   - No StatefulSet pods (avoid data loss)
   - No DaemonSet pods critical for monitoring
   - Longest idle time (based on CloudWatch metrics)
2. **Cordon Node**: `kubectl cordon <node>` (prevent new pod scheduling)
3. **Drain Workloads**: `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=5m`
   - Respects PodDisruptionBudgets (never violate min available replicas)
   - Waits for graceful pod termination (respects `terminationGracePeriodSeconds`)
4. **Verify Drain Success**: Check for remaining non-DaemonSet pods
5. **Terminate Instance**: EC2 TerminateInstances API call
6. **Cleanup**: Remove node from K3s cluster: `kubectl delete node <node-name>`

**Never Terminate Nodes Hosting**:

- System-critical pods: `kube-system` namespace (CoreDNS, metrics-server, kube-proxy)
- StatefulSet pods without replicas on other nodes
- Single-replica deployments (no redundancy)
- Pods with `PodDisruptionBudget` min available violations

**Failure Handling**:

- If drain times out after 5 minutes → abort scale-down, uncordon node, alert team
- If only 2 nodes remain → never scale down (maintain HA minimum)

### 5. State Management & Race Condition Prevention

**DynamoDB Schema**:

- **Table Name**: `k3s-autoscaler-state`
- **Partition Key**: `cluster_id` (String) - value: `smartscale-prod`
- **Attributes**:
  - `node_count` (Number) - current active worker nodes
  - `last_scale_time` (Number) - Unix timestamp of last scaling event
  - `scaling_in_progress` (String) - `"true"` when locked, absent when free
  - `lock_expiry` (Number) - Unix timestamp when lock auto-expires (5 min TTL)
  - `last_scale_action` (String) - `"scale_up"` or `"scale_down"`
  - `last_scale_reason` (String) - e.g., `"CPU 78%, pending pods 5"`

**Lock Mechanism**:

```python
# Acquire lock with conditional write
response = dynamodb.put_item(
    TableName='k3s-autoscaler-state',
    Item={'cluster_id': 'smartscale-prod', 'scaling_in_progress': 'true', 'lock_expiry': now + 300},
    ConditionExpression='attribute_not_exists(scaling_in_progress) OR lock_expiry < :now'
)
# If ConditionCheckFailedException → another Lambda holds lock, exit gracefully
```

**Lock Cleanup**:

- Lambda releases lock on successful completion or caught exceptions
- Expired locks (> 5 minutes old) auto-release on next invocation check
- DynamoDB TTL attribute removes stale state after 24 hours

**Lambda Timeout Handling**:

- If Lambda times out mid-scaling → lock remains until expiry (5 min)
- Next invocation checks for incomplete operations:
  - EC2 instances in `pending` state → wait for completion
  - Nodes stuck in `NotReady` → mark as failed, terminate instance
- Never leave cluster in inconsistent state

### 6. AWS Lambda Function Implementation

**Configuration**:

- **Runtime**: Python 3.11
- **Memory**: 256 MB
- **Timeout**: 60 seconds
- **Trigger**: EventBridge rule (cron: `rate(2 minutes)`)
  - **[BONUS]** Dynamic intervals: 2 min during peak (9 AM-9 PM), 5 min off-peak
- **VPC**: Same VPC as K3s cluster (for Prometheus connectivity)
- **Subnets**: Private subnets with NAT Gateway for internet access
- **Security Group**: Allow outbound to master node port 30090 (Prometheus NodePort)

**Environment Variables**:

```bash
PROMETHEUS_URL=http://<master-private-ip>:30090
DYNAMODB_TABLE=k3s-autoscaler-state
CLUSTER_ID=smartscale-prod
K3S_TOKEN_SECRET_ARN=arn:aws:secretsmanager:us-east-1:xxx:secret:k3s-token
MASTER_NODE_TAG=Role:k3s-master
MIN_NODES=2
MAX_NODES=10
SCALE_UP_THRESHOLD_CPU=70
SCALE_DOWN_THRESHOLD_CPU=30
COOLDOWN_SCALE_UP=300  # 5 minutes
COOLDOWN_SCALE_DOWN=600  # 10 minutes
SLACK_WEBHOOK_URL=<from Secrets Manager>
```

**Execution Flow**:

1. **Pre-flight Checks** (5s):
   - Query DynamoDB for current state
   - Check for in-progress scaling operations (exit if locked)
   - Verify last scaling event respects cooldown period
2. **Metric Collection** (10s):
   - Query Prometheus API: `/api/v1/query?query=<promql>`
   - Collect: CPU%, memory%, pending pods, node count, custom app metrics
   - Handle Prometheus unavailability gracefully (retry 2x, then use cached metrics)
3. **Decision Logic** (5s):
   - Evaluate scale-up conditions (OR logic)
   - Evaluate scale-down conditions (AND logic)
   - Calculate target node count (current ± 1-2)
   - Respect min/max constraints
4. **Scaling Execution** (30s):
   - **If scale-up**: Acquire DynamoDB lock → Launch EC2 instances → Poll for Ready status
   - **If scale-down**: Acquire lock → Select node → Drain → Terminate
   - **If no action**: Log "No scaling needed" and exit
5. **State Update & Notification** (10s):
   - Update DynamoDB: new node count, timestamp, scaling action
   - Release lock
   - Send Slack notification with details
   - Log event to CloudWatch with structured JSON

**IAM Policy** (Least Privilege):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstances",
        "ec2:CreateTags",
        "ec2:DescribeInstanceStatus"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:RequestedRegion": "us-east-1"
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem"
      ],
      "Resource": "arn:aws:dynamodb:us-east-1:*:table/k3s-autoscaler-state"
    },
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": [
        "arn:aws:secretsmanager:us-east-1:*:secret:k3s-token-*",
        "arn:aws:secretsmanager:us-east-1:*:secret:slack-webhook-*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:us-east-1:*:log-group:/aws/lambda/smartscale-autoscaler:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:CreateNetworkInterface",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DeleteNetworkInterface"
      ],
      "Resource": "*"
    }
  ]
}
```

**Python Dependencies** (`requirements.txt`):

```
boto3==1.34.18
requests==2.31.0
kubernetes==28.1.0
prometheus-api-client==0.5.3
```

**Lambda Layers** (for large dependencies):

- Instead of bundling all dependencies in deployment package, use Lambda layers for:
  - `boto3` and `botocore` (AWS SDK - already included in Lambda runtime, but pin version)
  - `kubernetes` library (large, reusable across Lambda updates)
- Benefits: Faster deployments, smaller zip files, dependency reuse
- Create layer: `zip -r python.zip python/` (dependencies in `python/` folder)
- Max layer size: 250MB unzipped

### 7. Monitoring, Logging & Alerting

#### Prometheus Configuration

**prometheus.yml**:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    cluster: "smartscale-prod"

scrape_configs:
  - job_name: "kubernetes-nodes"
    kubernetes_sd_configs:
      - role: node
    relabel_configs:
      - source_labels: [__meta_kubernetes_node_name]
        target_label: node

  - job_name: "kubernetes-pods"
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_name]
        target_label: pod

  - job_name: "kube-state-metrics"
    static_configs:
      - targets: ["kube-state-metrics:8080"]

  - job_name: "demo-app-metrics"
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        regex: demo-app
        action: keep
```

**Key PromQL Queries**:

```promql
# CPU Usage (%)
avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100

# Memory Usage (%)
(1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100

# Pending Pods
sum(kube_pod_status_phase{phase="Pending"})

# Node Count
count(kube_node_info)

# API Latency P95 (bonus)
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

# Error Rate (bonus)
sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m])) * 100
```

**Deployment**:

- Deploy Prometheus as StatefulSet on master node (persistent storage required)
- Deploy kube-state-metrics as Deployment (exposes K8s object metrics)
- PersistentVolume: 20GB EBS gp3 volume for Prometheus data (7-day retention)
- Resource limits: 500m CPU, 1GB RAM for Prometheus pod

\*\*kube-state-Installation & Configuration

**Deployment Options**:

**Option 1: Grafana as K3s Pod** (Recommended - cost-effective):

```bash
kubectl create namespace monitoring
helm repo add grafana https://grafana.github.io/helm-charts
helm install grafana grafana/grafana -n monitoring \
  --set persistence.enabled=true \
  --set persistence.size=10Gi \
  --set service.type=NodePort \
  --set adminPassword=<from-secrets-manager>
```

- Access via NodePort (e.g., http://master-ip:32000)
- Persistent storage: 10GB EBS volume
- Cost: $0 (uses existing master node)

**Option 2: Separate EC2 Instance** (better performance, higher cost):

- t3.micro instance: $7/month
- Standalone Grafana installation
- Separate from cluster (monitoring survives cluster issues)

**Data Source Configuration**:

- Add Prometheus as data source: http://prometheus-service:9090
- Test connection to ensure metrics flowing

#### Grafana metrics Installation\*\*:

```bash
kubectl apply -f https://github.com/kubernetes/kube-state-metrics/releases/download/v2.10.0/kube-state-metrics-standard.yaml
```

- Exposes metrics on port 8080: pod status, deployments, node info
- Required for: pending pod count, pod phase tracking

**Exposure**:

- Prometheus pod runs on master node
- Service type: NodePort (port 30090) - cheaper than LoadBalancer, no ELB costs
- Security Group: Allow port 30090 from Lambda security group only
- **[BONUS]** Basic Auth: username/password stored in Secrets Manager
- **Why not LoadBalancer?** Adds $16/month ELB cost, Lambda has VPC access already
- **Why not Ingress?** Requires Ingress controller (nginx/traefik), adds complexity

#### Grafana Dashboards

**Dashboard 1: Cluster Overview**

- Panel 1: Current node count (gauge)
- Panel 2: CPU utilization (time series, 24h view)
- Panel 3: Memory utilization (time series, 24h view)
- Panel 4: Pending pods (counter)
- Panel 5: Scaling events timeline (annotations)
- Panel 6: Pod distribution by node (heatmap)

**Dashboard 2: Autoscaler Performance**

- Panel 1: Lambda execution duration (histogram)
- Panel 2: Lambda invocation count (counter)
- Panel 3: Scaling decisions (scale-up vs. scale-down vs. no-action, pie chart)
- Panel 4: Node join latency (time to Ready, histogram)
- Panel 5: Cost savings estimate (calculated metric, gauge)

**Dashboard 3: Application Metrics** (Bonus)

- Panel 1: API request rate (QPS)
- Panel 2: API latency (p50, p95, p99)
- Panel 3: Error rate (%)
- Panel 4: Queue depth (if using RabbitMQ)

#### CloudWatch Metrics (Custom)

**Metrics to Publish**:

- `AutoscalerInvocations` (Count) - Lambda executions
- `ScaleUpEvents` (Count) - Dimension: `Reason` (CPU/Memory/Pending/Custom)
- `ScaleDownEvents` (Count)
- `ScalingFailures` (Count) - Dimension: `ErrorType` (LockTimeout/EC2QuotaExceeded/DrainFailed)
- `NodeJoinLatency` (Milliseconds) - Time from launch to Ready
- `LambdaExecutionTime` (Milliseconds) - End-to-end duration
- `CurrentNodeCount` (Gauge)
- `ClusterCPUUtilization` (Percent)
- `ClusterMemoryUtilization` (Percent)
- `PendingPods` (Count)

#### SNS Configuration

**SNS Topic Setup**:

- **Topic Name**: `smartscale-autoscaler-alerts`
- **Subscriptions**:
  - Email: `devops-team@techflow.com` (for critical alerts)
  - SMS: `+880-XXX-XXXX` (for emergency capacity issues)
  - Lambda: Slack webhook forwarder (transforms SNS → Slack format)
- **Access Policy**: Allow CloudWatch Alarms and Lambda to publish

**Message Format**:

```json
{
  "Subject": "🔴 SmartScale Alert: Scaling Failure",
  "Message": {
    "AlarmName": "ScalingFailureAlarm",
    "NewStateValue": "ALARM",
    "NewStateReason": "Lambda execution failed 3 times in 15 minutes",
    "Timestamp": "2025-12-22T10:30:00.000Z",
    "Region": "us-east-1"
  }
}
```

#### CloudWatch Alarms

**Critical Alarms** (SNS → Email/SMS + Slack):

1. **Scaling Failure**: 3+ scaling failures in 15 minutes
2. **CPU Overload**: Cluster CPU > 90% for 5+ minutes (capacity exhausted)
3. **At Max Capacity**: Node count = 10 for 10+ minutes (cannot scale further)
4. **Node Join Failure**: New instance not Ready after 5 minutes

**Warning Alarms** (Slack only):

1. **High Memory**: Memory > 80% for 10 minutes
2. **Lambda Timeout**: Execution time > 50 seconds (approaching 60s limit)
3. **Prometheus Unavailable**: Query failures for 5+ minutes

#### Slack Notifications

**Message Format**:

```
🟢 **Scale-Up Initiated**
Reason: CPU 78%, Pending pods: 5
Action: Added 2 nodes (t3.small)
New Total: 7 nodes
Estimated Cost Impact: +₹4,000/day
Time: 2025-12-22 10:15:00 UTC

🔵 **Scale-Down Completed**
Reason: CPU 25%, No pending pods
Action: Removed 1 node (i-0abc123)
New Total: 3 nodes
Estimated Savings: -₹2,000/day
Time: 2025-12-22 22:45:00 UTC

🔴 **Scaling Failure**
Reason: EC2 quota exceeded (max 10 instances)
Action: Scale-up aborted
Current: 10 nodes
Recommendation: Request quota increase or enable Spot instances
Time: 2025-12-22 11:00:00 UTC

⚠️ **Cluster at Maximum Capacity**
Current: 10 nodes, CPU 85%
Warning: Cannot scale further, service degradation possible
Recommendation: Optimize workloads or increase max node limit
Time: 2025-12-22 14:30:00 UTC
```

**Webhook Integration**:

- Slack webhook URL stored in AWS Secrets Manager
- Lambda sends POST request with formatted JSON payload
- Include link to Grafana dashboard for context

### 8. Security & Compliance

#### Secret Management

- **K3s Join Token**: AWS Secrets Manager (encryption at rest, rotation enabled)
- **Prometheus Credentials**: Secrets Manager (if using Basic Auth)
- **Slack Webhook URL**: Secrets Manager
- **Never** store secrets in:
  - User Data scripts (plain text)
  - Environment variables (visible in console)
  - Git repository (risk of exposure)
  - S3 buckets without encryption

#### IAM & Access Control

- **Lambda Execution Role**: Least-privilege policy (see section 6)
- **EC2 Instance Profile**: Allow ECR pulls, Secrets reads, CloudWatch writes
- **DynamoDB**: Table-level policies (no wildcard access)
- **No hardcoded credentials**: Use IAM roles exclusively
- **MFA**: Require for AWS Console access to production account

#### Network Security

- **VPC Isolation**: K3s cluster in private subnets
- **Security Groups**:
  - Master: Allow 6443/tcp from worker SG only
  - Workers: Allow all traffic from master SG
  - Prometheus: Allow 30090/tcp from Lambda SG only
  - NAT Gateway: Workers → Internet for package downloads
- **NACLs**: Default allow (security groups provide sufficient protection)
- **No Public IPs**: Workers use NAT Gateway for outbound traffic

#### Encryption

- **EBS Volumes**: Encrypted at rest (AWS-managed KMS key)
- **Secrets Manager**: AES-256 encryption
- **K3s Inter-node**: TLS 1.3 for etcd/API server communication
- **DynamoDB**: Server-side encryption enabled

#### Audit & Compliance

- **CloudTrail**: Log all EC2, Lambda, DynamoDB API calls
- **CloudWatch Logs**: Retain Lambda logs for 30 days (set retention policy in Pulumi)
  ```typescript
  const logGroup = new aws.cloudwatch.LogGroup("lambda-logs", {
    name: "/aws/lambda/smartscale-autoscaler",
    retentionInDays: 30,
  });
  ```
- **DynamoDB Streams**: Audit all state changes (optional)
- **Cost Explorer**: Tag all resources with `Project:SmartScale` for cost tracking

### 9. Cost Optimization

#### Target Savings

- **Current Cost**: 1.2 lakh BDT/month (5 nodes × 24,000 BDT)
- **Optimized Cost**: 60,000-70,000 BDT/month (40-50% reduction)
- **Breakdown**:
  - Off-peak (9 PM - 9 AM): 2 nodes = 24,000 BDT/month
  - Peak (9 AM - 9 PM): 5-7 nodes = 30,000 BDT/month
  - Lambda: ~500 BDT/month (15,000 invocations)
  - DynamoDB: ~200 BDT/month (on-demand, low traffic)
  - CloudWatch: ~300 BDT/month (custom metrics + logs)

#### **[BONUS] Spot Instance Integration**

**Strategy**:

- Mix: 60% Spot + 40% On-Demand
- Spot Savings: 60-70% off On-Demand pricing
- Interruption Handling:
  1. Subscribe to EC2 Spot Interruption Notices (2-minute warning)
  2. EventBridge rule triggers Lambda on interruption
  3. Lambda cordons node immediately
  4. Drain workloads with 90-second timeout (within 2-min window)
  5. Launch replacement On-Demand instance if Spot unavailable
- Diversification: Use 2+ instance types (t3.small, t3a.small) for higher availability

**Fallback Logic**:

```python
try:
    launch_spot_instances(count=2, instance_types=['t3.small', 't3a.small'])
except InsufficientSpotCapacity:
    launch_on_demand_instances(count=1, instance_type='t3.small')
    send_slack_alert("Spot unavailable, launched On-Demand fallback")
```

#### Cost Tracking

- **Tag all resources**:
  - `Project: SmartScale`
  - `Environment: Production`
  - `ManagedBy: Lambda`
  - `InstanceType: Spot/OnDemand`
- **CloudWatch Cost Metric**:
  - Calculate hourly cost based on instance count × type × pricing
  - Publish to CloudWatch for dashboard visualization
- **Weekly Cost Report**:
  - Lambda generates summary (total hours, cost breakdown, savings %)
  - Send to Slack every Monday 9 AM

#### **[BONUS] Predictive Scaling ROI**

- Pre-scaling 10 minutes early reduces customer-facing latency spikes
- Avoids emergency scaling during flash sales (prevent revenue loss)
- Estimated value: 2-5 lakh BDT/month in prevented downtime costs

### 10. Infrastructure as Code (Pulumi TypeScript)

#### Demo Application Deployment

**Source**: https://sd1finalproject.lovable.app (provided Flask/React app)

**Deployment Workflow**:

1. **Containerize Demo App**:

   ```dockerfile
   # demo-app/Dockerfile
   FROM node:20-alpine
   WORKDIR /app
   COPY package*.json ./
   RUN npm install --production
   COPY . .
   EXPOSE 3000
   CMD ["npm", "start"]
   ```

2. **Build & Push to ECR**:

   ```bash
   aws ecr create-repository --repository-name smartscale-demo-app
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com
   docker build -t smartscale-demo-app:latest demo-app/
   docker tag smartscale-demo-app:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/smartscale-demo-app:latest
   docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/smartscale-demo-app:latest
   ```

3. **ECR Authentication for K3s**:

   - Create IAM role for worker nodes with `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage` permissions
   - Attach role to EC2 instance profile (already done in worker IAM setup)
   - K3s automatically uses instance profile credentials to pull from ECR (no imagePullSecrets needed)

4. **Deploy to K3s**:

   ```yaml
   # k8s/demo-app-deployment.yaml
   apiVersion: apps/v1
   kind: Deployment
   metadata:
     name: demo-app
     labels:
       app: demo-app
   spec:
     replicas: 5
     selector:
       matchLabels:
         app: demo-app
     template:
       metadata:
         labels:
           app: demo-app
       spec:
         containers:
           - name: demo-app
             image: <account-id>.dkr.ecr.us-east-1.amazonaws.com/smartscale-demo-app:latest
             ports:
               - containerPort: 3000
             resources:
               requests:
                 cpu: 200m
                 memory: 256Mi
               limits:
                 cpu: 500m
                 memory: 512Mi
             livenessProbe:
               httpGet:
                 path: /health
                 port: 3000
               initialDelaySeconds: 30
               periodSeconds: 10
             readinessProbe:
               httpGet:
                 path: /ready
                 port: 3000
               initialDelaySeconds: 10
               periodSeconds: 5
   ---
   apiVersion: v1
   kind: Service
   metadata:
     name: demo-app-service
   spec:
     type: NodePort
     selector:
       app: demo-app
     ports:
       - protocol: TCP
         port: 80
         targetPort: 3000
         nodePort: 30080
   ```

5. **Apply Manifests**:

   ```bash
   kubectl apply -f k8s/demo-app-deployment.yaml
   kubectl get pods -l app=demo-app  # Verify pods running
   ```

6. **Access Demo App**:
   - NodePort: `http://<master-public-ip>:30080`
   - **[BONUS]** Use AWS ALB Ingress Controller for production-grade Load Balancer

### 10. Infrastructure as Code (Pulumi TypeScript)

#### Project Structure

```
pulumi/
├── index.ts                 # Main entrypoint, stack exports
├── vpc.ts                   # VPC, subnets, NAT gateway, route tables
├── securityGroups.ts        # SGs for master, workers, Lambda, Prometheus
├── iam.ts                   # Roles for Lambda, EC2 instance profile
├── ec2.ts                   # SNS topics, CloudWatch alarms
├── demoApp.ts               # (Optional) Demo app K8s manifests if deploying via Pulumiairs
├── lambda.ts                # Lambda function, EventBridge trigger, layers
├── dynamodb.ts              # State table with lock attributes
├── secrets.ts               # Secrets Manager for K3s token, Slack webhook
├── cloudwatch.ts            # Alarms, custom metrics, log groups
├── ecr.ts                   # Container registry for demo app
├── monitoring.ts            # Grafana/Prometheus setup (if not in K3s)
├── package.json             # Pulumi AWS SDK, TypeScript types
└── Pulumi.yaml              # Stack config (region, node count limits)
```

#### Key Resources to Define

**VPC & Networking**:

- VPC with CIDR `10.0.0.0/16`
- Public subnets (2 AZs): `10.0.1.0/24`, `10.0.2.0/24`
- Private subnets (2 AZs): `10.0.11.0/24`, `10.0.12.0/24`
- NAT Gateway in each public subnet (HA)
- Internet Gateway
- Route tables: Public (IGW), Private (NAT)

**EC2 Instance Profile** (Workers):

```typescript
const workerRole = new aws.iam.Role("k3s-worker-role", {
  assumeRolePolicy: JSON.stringify({
    Version: "2012-10-17",
    Statement: [
      {
        Action: "sts:AssumeRole",
        Effect: "Allow",
        Principal: { Service: "ec2.amazonaws.com" },
      },
    ],
  }),
});

// ECR Pull Permissions
new aws.iam.RolePolicyAttachment("worker-ecr-policy", {
  role: workerRole.name,
  policyArn: "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
});

// Secrets Manager Read for K3s Token
new aws.iam.RolePolicy("worker-secrets-policy", {
  role: workerRole.name,
  policy: JSON.stringify({
    Version: "2012-10-17",
    Statement: [
      {
        Effect: "Allow",
        Action: ["secretsmanager:GetSecretValue"],
        Resource: k3sTokenSecret.arn,
      },
    ],
  }),
});

// CloudWatch Logs Write
new aws.iam.RolePolicyAttachment("worker-cloudwatch-policy", {
  role: workerRole.name,
  policyArn: "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
});

const workerInstanceProfile = new aws.iam.InstanceProfile(
  "k3s-worker-profile",
  {
    role: workerRole.name,
  }
);
```

**Master Node Instance Profile**:

```typescript
const masterRole = new aws.iam.Role("k3s-master-role", {
  assumeRolePolicy: JSON.stringify({
    Version: "2012-10-17",
    Statement: [
      {
        Action: "sts:AssumeRole",
        Effect: "Allow",
        Principal: { Service: "ec2.amazonaws.com" },
      },
    ],
  }),
});

// ECR for Prometheus/monitoring images
new aws.iam.RolePolicyAttachment("master-ecr-policy", {
  role: masterRole.name,
  policyArn: "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
});

// Secrets Manager (if needed for configs)
new aws.iam.RolePolicy("master-secrets-policy", {
  role: masterRole.name,
  policy: JSON.stringify({
    Version: "2012-10-17",
    Statement: [
      {
        Effect: "Allow",
        Action: ["secretsmanager:GetSecretValue"],
        Resource: "*",
      },
    ],
  }),
});

const masterInstanceProfile = new aws.iam.InstanceProfile(
  "k3s-master-profile",
  {
    role: masterRole.name,
  }
);
```

**EC2 Launch Template** (Workers):

````typescript
const workerLaunchTemplate = new aws.ec2.LaunchTemplate("k3s-worker", {
    imageId: "ami-0c55b159cbfafe1f0", // Amazon Linux 2023
    instanceType: "t3.small",
    keyName: keyPair.keyName,
    iamInstanceProfile: { arn: workerInstanceProfile.arn },
    userData: pulumi.output(masterInstance.privateIp).apply(ip =>
        Buffer.from(generateWorkerUserData(ip)).toString('base64')
    ),
    blockDeviceMappings: [{
        deviceName: "/dev/xvda",
        ebs: {
            volumeSize: 20,
**EventBridge Rule**:
```typescript
const schedulerRule = new aws.cloudwatch.EventRule("autoscaler-trigger", {
    scheduleExpression: "rate(2 minutes)",
    description: "Trigger SmartScale autoscaler every 2 minutes",
});

new aws.cloudwatch.EventTarget("lambda-target", {
    rule: schedulerRule.name,
    arn: autoscalerLambda.arn,
});

new aws.lambda.Permission("allow-eventbridge", {
    action: "lambda:InvokeFunction",
    function: autoscalerLambda.name,
    principal: "events.amazonaws.com",
    sourceArn: schedulerRule.arn,
});
````

**SNS Topic for Alerts**:

```typescript
const alertTopic = new aws.sns.Topic("smartscale-alerts", {
  displayName: "SmartScale Autoscaler Alerts",
});

// Email subscription
new aws.sns.TopicSubscription("email-subscription", {
  topic: alertTopic.arn,
  protocol: "email",
  endpoint: "devops@techflow.com",
});

// SMS subscription (optional)
new aws.sns.TopicSubscription("sms-subscription", {
  topic: alertTopic.arn,
  protocol: "sms",
  endpoint: "+880XXXXXXXXX",
});

// Lambda subscription for Slack forwarding
new aws.sns.TopicSubscription("slack-forwarder", {
  topic: alertTopic.arn,
  protocol: "lambda",
  endpoint: slackForwarderLambda.arn,
});
```

**ECR Repository**:

```typescript
const demoAppRepo = new aws.ecr.Repository("smartscale-demo-app", {
  imageScanningConfiguration: {
    scanOnPush: true,
  },
  imageTagMutability: "MUTABLE",
  encryptionConfiguration: {
    encryptionType: "AES256",
  },
});

// Lifecycle policy to keep only last 10 images
new aws.ecr.LifecyclePolicy("demo-app-lifecycle", {
  repository: demoAppRepo.name,
  policy: JSON.stringify({
    rules: [
      {
        rulePriority: 1,
        description: "Keep last 10 images",
        selection: {
          tagStatus: "any",
          countType: "imageCountMoreThan",
          countNumber: 10,
        },
        action: {
          type: "expire",
        },
      },
    ],
  }),
});
```

            volumeType: "gp3",
            encrypted: true,
        },
    }],
    tagSpecifications: [{
        resourceType: "instance",
        tags: {
            Name: "k3s-worker",
            Project: "SmartScale",
            ManagedBy: "Lambda",
        },
    }],
    vpcSecurityGroupIds: [workerSg.id],

});

````

**Lambda Function**:
```typescript
const autoscalerLambda = new aws.lambda.Function("smartscale-autoscaler", {
    runtime: aws.lambda.Runtime.Python3d11,
    handler: "autoscaler.handler",
    code: new pulumi.asset.AssetArchive({
        ".": new pulumi.asset.FileArchive("../lambda"),
    }),
    timeout: 60,
    memorySize: 256,
    role: lambdaRole.arn,
    environment: {
        variables: {
            PROMETHEUS_URL: pulumi.interpolate`http://${masterInstance.privateIp}:30090`,
            DYNAMODB_TABLE: stateTable.name,
            K3S_TOKEN_SECRET_ARN: k3sTokenSecret.arn,
            MIN_NODES: "2",
            MAX_NODES: "10",
        },
    },
    vpcConfig: {
        subnetIds: privateSubnetIds,
        securityGroupIds: [lambdaSg.id],
    },
});
````

**DynamoDB Table**:

```typescript
const stateTable = new aws.dynamodb.Table("k3s-autoscaler-state", {
  attributes: [{ name: "cluster_id", type: "S" }],
  hashKey: "cluster_id",
  billingMode: "PAY_PER_REQUEST",
  ttl: {
    attributeName: "ttl",
    enabled: true,
  },
  serverSideEncryption: {
    enabled: true,
  },
});
```

**EventBridge Rule**:

```typescript
const schedulerRule = new aws.cloudwatch.EventRule("autoscaler-trigger", {
  scheduleExpression: "rate(2 minutes)",
});

new aws.cloudwatch.EventTarget("lambda-target", {
  rule: schedulerRule.name,
  arn: autoscalerLambda.arn,
});

new aws.lambda.Permission("allow-eventbridge", {
  action: "lambda:InvokeFunction",
  function: autoscalerLambda.name,
  principal: "events.amazonaws.com",
  sourceArn: schedulerRule.arn,
});
```

#### Pulumi Exports

```typescript
export const masterIp = masterInstance.privateIp;
export const masterPublicIp = masterInstance.publicIp;
export const prometheusUrl = pulumi.interpolate`http://${masterInstance.privateIp}:30090`;
export const lambdaFunctionArn = autoscalerLambda.arn;
export const dynamodbTableName = stateTable.name;
export const vpcId = vpc.id;
export const snsTopicArn = alertTopic.arn;
export const ecrRepositoryUrl = demoAppRepo.repositoryUrl;
export const demoAppUrl = pulumi.interpolate`http://${masterInstance.publicIp}:30080`;
```

#### Deployment Commands

```bash
cd pulumi
npm install
pulumi stack init smartscale-prod
pulumi config set aws:region us-east-1
pulumi up  # Preview and deploy
pulumi stack output masterPublicIp  # Get master IP for SSH
pulumi destroy  # Tear down (only for dev/testing)
```

---

## 🧪 Testing Strategy

### 1. Unit Tests (Lambda Logic)

**Framework**: pytest

**Test Cases**:

- `test_scale_up_decision_cpu_high()`: CPU > 70% → should return "scale_up"
- `test_scale_down_decision_cpu_low()`: CPU < 30% for 10 min → "scale_down"
- `test_no_action_within_cooldown()`: Last scale 3 min ago → "no_action"
- `test_min_node_constraint()`: 2 nodes, scale-down signal → "no_action"
- `test_max_node_constraint()`: 10 nodes, scale-up signal → alert + "no_action"
- `test_dynamodb_lock_acquired()`: Lock free → should acquire successfully
- `test_dynamodb_lock_contention()`: Lock held → should exit gracefully
- `test_prometheus_unavailable()`: Query fails → use cached metrics or abort

**Run**:

```bash
cd lambda
pip install pytest moto boto3-stubs
pytest tests/ -v --cov=autoscaler
```

### 2. Integration Tests (Simulated AWS)

**Framework**: LocalStack (mock AWS services)

**Setup**:

```bash
docker run -d -p 4566:4566 localstack/localstack
export AWS_ENDPOINT_URL=http://localhost:4566
```

**Test Scenarios**:

- End-to-end Lambda execution with mock DynamoDB + EC2
- Verify EC2 RunInstances called with correct launch template
- Verify DynamoDB lock acquired/released properly
- Simulate Lambda timeout → verify lock cleanup

### 3. Load Testing (k6)

**Test Script** (`tests/load-test.js`):

```javascript
import http from "k6/http";
import { check, sleep } from "k6";

export let options = {
  stages: [
    { duration: "5m", target: 50 }, // Gradual ramp-up
    { duration: "10m", target: 200 }, // Flash sale spike
    { duration: "5m", target: 50 }, // Cool-down
    { duration: "5m", target: 0 }, // Scale-down test
  ],
  thresholds: {
    http_req_duration: ["p(95)<2000"], // 95% under 2s
    http_req_failed: ["rate<0.05"], // Error rate < 5%
  },
};

export default function () {
  const res = http.get("http://<load-balancer-url>/api/products");
  check(res, {
    "status is 200": (r) => r.status === 200,
    "response time < 2s": (r) => r.timings.duration < 2000,
  });
  sleep(1);
}
```

**Execution**:

```bash
k6 run tests/load-test.js --vus 100 --duration 30m
```

**Expected Behavior**:

- At 100+ VUs: CPU > 70%, autoscaler triggers scale-up within 3 min
- New nodes join, load distributes, response time stabilizes
- After load decreases: CPU < 30%, scale-down after 10+ min cooldown

### 4. Scale-Up Test Script

**Manual Trigger** (`tests/test-scale-up.sh`):

```bash
#!/bin/bash
echo "Simulating high CPU load on all nodes..."
kubectl run cpu-burn --image=progrium/stress -- --cpu 2 --timeout 600s
kubectl scale deployment cpu-burn --replicas=10

echo "Monitoring autoscaler response..."
watch -n 10 "kubectl get nodes; aws ec2 describe-instances --filters 'Name=tag:Project,Values=SmartScale' --query 'Reservations[*].Instances[*].[InstanceId,State.Name]'"

# Expected: Within 3 minutes, new EC2 instances launch and join cluster
```

### 5. Scale-Down Test Script

**Manual Trigger** (`tests/test-scale-down.sh`):

```bash
#!/bin/bash
echo "Reducing load to trigger scale-down..."
kubectl delete deployment cpu-burn

echo "Waiting for 10-minute cooldown + low CPU detection..."
sleep 700  # 11 minutes

echo "Checking for node termination..."
watch -n 10 "kubectl get nodes; aws ec2 describe-instances --filters 'Name=tag:Project,Values=SmartScale'"

# Expected: After 10+ minutes of CPU < 30%, one node drained and terminated
```

### 6. Failure Scenario Tests

**Scenario 1: Lambda Timeout During Scaling**

```bash
# Simulate slow Prometheus response
kubectl exec -n monitoring prometheus-0 -- killall -STOP prometheus
# Lambda times out, lock held
# Next invocation should detect expired lock, clean up, retry
```

**Scenario 2: EC2 Quota Exceeded**

```bash
# Manually launch 10 instances to hit limit
aws ec2 run-instances --count 10 --instance-type t3.small ...
# Lambda attempts scale-up, catches LimitExceededException
# Should send Slack alert, log error, not crash
```

**Scenario 3: Node Join Failure**

```bash
# Launch instance with invalid K3s token
aws secretsmanager update-secret --secret-id k3s-token --secret-string "invalid-token"
# Worker fails to join, Lambda detects NotReady after 5 min
# Should terminate instance, log failure, alert team
```

**Scenario 4: Drain Timeout**

```bash
# Deploy StatefulSet with slow graceful shutdown
kubectl apply -f tests/stateful-app.yaml  # terminationGracePeriodSeconds: 600
# Trigger scale-down, drain times out after 5 min
# Lambda should abort termination, uncordon node, alert
```

---

## 📦 Deliverables

### 1. GitHub Repository

**Repository Name**: `node-fleet` (current repo)

**Commit Frequency**: At end of every **4-5 hour lab session**

**Commit Message Format**:

```
[Session N] Brief description of changes

- Detailed bullet points of what was implemented
- Files added/modified
- Next steps

Signed-off-by: Your Name <email@example.com>
```

**Example**:

```
[Session 3] Implemented Lambda autoscaler logic

- Added scaling decision algorithm in autoscaler.py
- Integrated DynamoDB lock mechanism
- Unit tests for scale-up/down conditions
- Next: Test with real Prometheus metrics

Signed-off-by: Bayajid Alam <bayajid@example.com>
```

### 2. README.md Contents

**Required Sections**:

1. **Project Overview**
   - Business problem statement (TechFlow Solutions context)
   - Success criteria and goals
2. **Architecture Explanation**
   - High-level system diagram (draw.io or Lucidchart)
   - Component interaction flow diagram
   - Network topology (VPC, subnets, security groups)
3. **Technology Stack**
   - Pulumi (TypeScript) - why chosen
   - AWS services used - justification for each
   - Python 3.11 for Lambda - benefits
   - k6 for load testing - why over alternatives
4. **Setup Instructions**
   - Prerequisites (AWS account, Pulumi CLI, kubectl)
   - Environment variables required
   - Deployment steps (Pulumi up, K3s master setup, worker join)
   - Verification commands
5. **Lambda Function Logic**
   - Scaling algorithm flowchart
   - Code walkthrough of key functions
   - Error handling patterns
6. **Prometheus Configuration**
   - prometheus.yml explained
   - PromQL queries with rationale
   - Grafana dashboard screenshots
7. **DynamoDB Schema**
   - Table design with example items
   - Lock mechanism explanation
8. **Testing Results**
   - k6 load test graphs (RPS, latency, error rate)
   - Scale-up/down event logs
   - Cost comparison table (before/after)
9. **Troubleshooting Guide**
   - Common issues and solutions
   - How to check Lambda logs
   - How to manually intervene if autoscaler fails
10. **Cost Analysis**
    - Monthly cost breakdown (current vs. optimized)
    - Savings percentage achieved
    - ROI calculation
11. **Team Members** (if group project)
    - Names, roles, contributions

### 3. Technical Documentation

**Location**: `docs/` folder

**Files Required**:

- `ARCHITECTURE.md`: Detailed system design with diagrams
- `SCALING_ALGORITHM.md`: Decision logic flowchart and pseudocode
- `DEPLOYMENT_GUIDE.md`: Step-by-step deployment runbook
- `TROUBLESHOOTING.md`: Common errors and fixes
- `COST_ANALYSIS.md`: Detailed cost breakdown and savings report
- `SECURITY_CHECKLIST.md`: All security measures implemented
- `TESTING_RESULTS.md`: k6 graphs, CloudWatch metrics, scaling event logs

**Diagrams** (`docs/diagrams/`):

- `architecture-overview.png`: Complete system architecture
- `network-topology.png`: VPC, subnets, security groups, routing
- `scaling-logic-flowchart.png`: Decision tree for scale-up/down
- `data-flow.png`: Prometheus → Lambda → EC2 → K3s sequence
- `cost-comparison-chart.png`: Before/after cost visualization

### 4. Live Demo Requirements

**Presentation Format**: 20-30 minutes

**Sections**:

1. **Problem Statement** (3 min): TechFlow's pain points, business impact
2. **Solution Architecture** (5 min): Walk through diagrams, explain component choices
3. **Live Demo** (10 min):
   - Show current cluster state (kubectl get nodes)
   - Trigger load test (k6 run load-test.js)
   - Watch autoscaler logs in CloudWatch
   - Observe new nodes joining (kubectl get nodes -w)
   - Show Grafana dashboard (CPU/memory trends, scaling events)
   - Demonstrate scale-down after load drops
4. **Code Walkthrough** (5 min):
   - Open Lambda function in GitHub
   - Explain key sections: metric collection, decision logic, EC2 API calls
   - Show DynamoDB lock mechanism
5. **Cost Analysis** (3 min):
   - Show CloudWatch cost dashboard
   - Compare monthly bills (before: 1.2L BDT, after: 60K BDT)
   - Explain ROI and payback period
6. **Q&A** (5 min): Answer questions, discuss trade-offs

**Demo Environment**:

- Use AWS Free Tier t3.micro instances if possible (lower cost)
- OR record demo video in advance (in case live AWS costs too much)
- Have backup slides in case network issues

---

## 🏆 Evaluation Criteria

| **Criteria**                              | **Weight** | **Key Indicators**                                                                |
| ----------------------------------------- | ---------- | --------------------------------------------------------------------------------- |
| **Architecture Design & AWS Integration** | 20%        | Proper VPC setup, security groups, IAM roles, service connectivity                |
| **Lambda Autoscaler Implementation**      | 20%        | Clean code, error handling, correct scaling logic, DynamoDB lock mechanism        |
| **Prometheus Monitoring Setup**           | 15%        | Correct PromQL queries, Grafana dashboards, metric collection working             |
| **Graceful Scaling Logic (Up & Down)**    | 15%        | Nodes join automatically, drain safely, no service disruption                     |
| **Security & IAM Best Practices**         | 10%        | Secrets Manager usage, least-privilege IAM, encrypted volumes, no hardcoded creds |
| **Documentation & Clarity**               | 10%        | Comprehensive README, clear diagrams, runbooks, troubleshooting guide             |
| **Presentation & Live Demo**              | 10%        | Clear explanation, working demo, confident delivery, Q&A handling                 |

**Bonus Points** (up to +15%):

- **Spot Instance Integration** (+5%): Mix Spot/On-Demand, handle interruptions
- **Predictive Scaling** (+3%): Pre-scale based on historical patterns
- **Custom App Metrics** (+2%): Queue depth, API latency in scaling logic
- **Multi-AZ Awareness** (+2%): Balance workers across zones
- **GitOps Config Management** (+2%): FluxCD/ArgoCD integration
- **Cost Dashboard** (+1%): Real-time cost tracking visualization

---

## 🌟 Bonus Challenges (Optional Enhancements)

### 1. Multi-AZ Awareness

**Goal**: Ensure workers are evenly distributed across availability zones for resilience.

**Implementation**:

- Lambda tracks node count per AZ in DynamoDB
- Scale-up: Launch new instance in AZ with fewest nodes
- Scale-down: Remove node from AZ with most nodes
- Maintain at least 1 node per AZ (never leave zone empty)

**Code Snippet**:

```python
def select_scale_down_node():
    nodes_by_az = count_nodes_per_az()
    # Find AZ with most nodes
    max_az = max(nodes_by_az, key=nodes_by_az.get)
    # Select node with fewest pods in that AZ
    return get_least_loaded_node(az=max_az)
```

### 2. Spot Instance Integration

**Goal**: Reduce costs by 60-70% using Spot instances with interruption handling.

**Implementation**:

- Launch template specifies `instanceMarketOptions.marketType = "spot"`
- Subscribe to EC2 Spot Interruption Warnings (2-minute notice)
- EventBridge rule triggers Lambda on interruption
- Lambda cordons + drains node within 90 seconds
- Launch replacement (prefer Spot, fallback to On-Demand)

**Interruption Handler Lambda**:

```python
def handle_spot_interruption(event):
    instance_id = event['detail']['instance-id']
    node_name = get_node_name(instance_id)
    kubectl_cordon(node_name)
    kubectl_drain(node_name, timeout=90)
    launch_replacement_instance(prefer_spot=True)
```

### 3. Predictive Scaling

**Goal**: Pre-scale 10 minutes before known traffic spikes.

**Implementation**:

- Store last 7 days of CPU/traffic data in DynamoDB
- Calculate average load by hour-of-day and day-of-week
- Detect patterns: "Every weekday 9 AM, CPU goes 30% → 75%"
- At 8:50 AM, pre-scale from 2 → 5 nodes
- Validate predictions: compare actual vs. predicted, adjust model

**Prediction Logic**:

```python
def predict_load(current_time):
    historical_data = get_last_7_days_metrics()
    same_hour_avg = average(historical_data, hour=current_time.hour)
    if same_hour_avg > 70:
        return "scale_up"
    return "no_action"
```

### 4. Custom Application Metrics

**Goal**: Scale based on application-specific signals, not just CPU/memory.

**Metrics to Collect**:

- **Queue Depth**: RabbitMQ/SQS message count
  - Scale-up if > 1000 messages
  - Scale-down if < 100 messages for 10 min
- **API Latency**: p95 response time
  - Scale-up if p95 > 2 seconds
  - Indicates backend is overwhelmed
- **Error Rate**: 5xx responses / total requests
  - Scale-up if > 5% for 2+ minutes
  - Prevents cascading failures

**PromQL Queries**:

```promql
# Queue depth
rabbitmq_queue_messages{queue="orders"}

# API latency p95
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{endpoint="/api/checkout"}[5m]))

# Error rate
sum(rate(http_requests_total{status=~"5.."}[2m])) / sum(rate(http_requests_total[2m])) * 100
```

### 5. GitOps Configuration Management

**Goal**: Version-control all K8s manifests, auto-deploy on Git push.

**Tools**: FluxCD or ArgoCD

**Workflow**:

1. All K8s YAML files stored in Git repo (`k8s/` folder)
2. FluxCD watches repo for changes every 1 minute
3. On commit to `main`, FluxCD applies changes to cluster
4. Rollback: `git revert` → automatic revert in cluster
5. Audit trail: All config changes tracked in Git history

**FluxCD Setup**:

```bash
flux bootstrap github \
  --owner=BayajidAlam \
  --repository=node-fleet \
  --path=k8s \
  --personal
```

### 6. Slack Notifications (Detailed)

**Goal**: Send actionable alerts with context and troubleshooting links.

**Message Structure**:

- **Emoji** for visual categorization (🟢 success, 🔴 failure, ⚠️ warning)
- **Action taken** (added/removed nodes, counts)
- **Reason** (CPU%, pending pods, custom metrics)
- **Impact** (cost change, current capacity)
- **Links** (Grafana dashboard, CloudWatch logs)
- **Recommendations** (if at capacity, suggest quota increase)

**Implementation**:

```python
def send_slack_notification(event_type, details):
    webhook_url = get_secret("slack-webhook-url")
    message = {
        "text": f"{details['emoji']} {event_type}",
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": details['summary']}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Reason:* {details['reason']}"},
                {"type": "mrkdwn", "text": f"*New Total:* {details['node_count']} nodes"},
            ]},
            {"type": "actions", "elements": [
                {"type": "button", "text": "View Dashboard", "url": details['dashboard_url']},
            ]},
        ],
    }
    requests.post(webhook_url, json=message)
```

### 7. Cost Dashboard (Real-Time Tracking)

**Goal**: Visualize spending in real-time, compare against budget.

**Metrics to Display**:

- **Current hourly cost**: Nodes × instance type pricing
- **Daily cost projection**: Current cost × 24
- **Monthly cost trend**: Graph of last 30 days
- **Savings vs. baseline**: (120,000 BDT - current) / 120,000 × 100%
- **Cost per request**: Total cost / API request count
- **Spot vs. On-Demand ratio**: % of capacity on Spot

**Implementation**:

- Lambda publishes cost metric to CloudWatch every 5 minutes
- Grafana queries CloudWatch for cost data
- Panel 1: Gauge showing current daily cost (target: < 2,000 BDT)
- Panel 2: Line graph of monthly cost trend
- Panel 3: Pie chart of cost breakdown (EC2, Lambda, data transfer)

**Grafana Query**:

```
SELECT SUM(node_count * hourly_rate) FROM cloudwatch WHERE metric_name='EC2Cost' LAST 24h
```

---

## 🚀 Project Timeline (4-Week Sprint)

### Week 1: Infrastructure Setup

- **Session 1** (4-5h):
  - AWS account setup, configure AWS CLI with credentials
  - Install Pulumi CLI, Node.js, kubectl
  - Initialize Pulumi project: `pulumi new aws-typescript`
  - Create VPC, subnets, Internet Gateway, NAT Gateway, route tables
  - **Commit**: "Session 1: VPC and network infrastructure setup"
- **Session 2** (4-5h):
  - Create security groups (master, worker, Lambda)
  - Generate EC2 key pair for SSH access
  - Create IAM roles (Lambda execution, EC2 instance profiles)
  - Deploy K3s master node with User Data script
  - SSH to master, verify K3s running: `sudo kubectl get nodes`
  - **Commit**: "Session 2: K3s master node deployment and IAM setup"
- **Session 3** (4-5h):
  - Create EC2 launch template for workers
  - Store K3s token in Secrets Manager
  - Manually test worker join (launch 1 instance from template)
  - Verify worker joins cluster: `kubectl get nodes`
  - Debug any join issues (security groups, token retrieval)
  - **Commit**: "Session 3: Worker node launch template and join automation"
- **Session 4** (4-5h):
  - Deploy Prometheus as StatefulSet on master
  - Deploy kube-state-metrics
  - Configure prometheus.yml with scrape configs
  - Expose Prometheus via NodePort (30090)
  - Test Prometheus UI: `http://<master-ip>:30090`
  - Verify metrics collection: run sample PromQL queries
  - **Commit**: "Session 4: Prometheus and kube-state-metrics deployment"
- **Deliverable**: K3s cluster with 1 master + 2 workers, Prometheus collecting metrics

### Week 2: Lambda Autoscaler Development

- **Session 5** (4-5h):
  - Create Lambda function scaffold in Python
  - Create DynamoDB state table with Pulumi
  - Test DynamoDB read/write from local Python script
  - Implement lock acquisition/release logic
  - Unit tests for lock mechanism
  - **Commit**: "Session 5: Lambda scaffold and DynamoDB state management"
- **Session 6** (4-5h):
  - Implement Prometheus query function (fetch CPU, memory, pending pods)
  - Implement scaling decision logic (evaluate thresholds)
  - Write unit tests for decision algorithm
  - Test locally with mocked Prometheus responses
  - **Commit**: "Session 6: Prometheus integration and scaling decision logic"
- **Session 7** (4-5h):
  - Implement EC2 launch function (RunInstances API)
  - Implement EC2 terminate function (TerminateInstances API)
  - Test scale-up: manually trigger Lambda, verify instance launches
  - Test scale-down: verify node drained and terminated
  - **Commit**: "Session 7: EC2 scaling operations implementation"
- **Session 8** (4-5h):
  - Deploy Lambda to AWS with Pulumi
  - Configure VPC networking for Lambda (private subnets)
  - Set up EventBridge trigger (2-minute schedule)
  - Test end-to-end: EventBridge → Lambda → EC2 → K3s
  - Debug timeout issues, optimize execution time
  - **Commit**: "Session 8: Lambda deployment and EventBridge integration"
- **Deliverable**: Working Lambda autoscaler triggering every 2 minutes, scaling nodes

### Week 3: Monitoring, Testing & Refinement

- **Session 9** (4-5h):
  - Install Grafana on K3s cluster (Helm chart)
  - Create 3 dashboards (Cluster Overview, Autoscaler Performance, App Metrics)
  - Configure CloudWatch custom metrics (publish from Lambda)
  - Set up CloudWatch log retention (30 days)
  - **Commit**: "Session 9: Grafana dashboards and CloudWatch metrics"
- **Session 10** (4-5h):
  - Create SNS topic and subscriptions (email, SMS, Lambda-Slack forwarder)
  - Configure CloudWatch alarms (scaling failures, CPU overload, max capacity)
  - Implement Slack notification function in Lambda
  - Test alert flow: trigger alarm → SNS → Slack
  - **Commit**: "Session 10: SNS alerts and Slack notifications"
- **Session 11** (4-5h):
  - Deploy demo app to K3s (from https://sd1finalproject.lovable.app)
  - Build Docker image, push to ECR
  - Create k6 load test script (gradual ramp, spike, cool-down)
  - Run load test: `k6 run tests/load-test.js --vus 100 --duration 20m`
  - Observe autoscaler behavior: nodes scale up during spike
  - **Commit**: "Session 11: Demo app deployment and k6 load testing"
- **Session 12** (4-5h):
  - Test failure scenarios (Lambda timeout, EC2 quota, Prometheus down, drain timeout)
  - Implement graceful error handling and retries
  - Verify scale-down safety (no pod disruptions)
  - Fix bugs discovered during testing
  - Optimize Lambda execution time (reduce from 50s → 30s)
  - **Commit**: "Session 12: Failure scenario testing and bug fixes"
- **Deliverable**: Fully tested autoscaler with comprehensive monitoring and alerting

### Week 4: Documentation, Optimization & Presentation

- **Session 13** (4-5h):
  - Write comprehensive README.md (all required sections)
  - Create architecture diagrams (draw.io: system overview, network topology, data flow)
  - Document scaling algorithm with flowchart
  - Write runbooks in `docs/` folder (deployment, troubleshooting)
  - **Commit**: "Session 13: Documentation and architecture diagrams"
- **Session 14** (4-5h):
  - Perform cost analysis (compare before/after autoscaler)
  - Calculate monthly savings: baseline 120K BDT → optimized 60-70K BDT
  - Create cost dashboard in Grafana
  - Security audit: verify IAM policies, encryption, no hardcoded secrets
  - Update IAM policies to least-privilege
  - **Commit**: "Session 14: Cost analysis and security audit"
- **Session 15** (4-5h):
  - **[BONUS]** Implement Spot instance integration (if time permits)
  - **[BONUS]** Implement predictive scaling based on historical data
  - Rehearse live demo (prepare demo script, timing)
  - Create presentation slides (problem, solution, architecture, demo, results)
  - **Commit**: "Session 15: Bonus features and demo preparation"
- **Session 16** (4-5h):
  - Final demo dry run with screen recording (backup if live demo fails)
  - Final code cleanup and comments
  - Update README with final metrics and screenshots
  - Prepare Q&A answers (anticipated questions)
  - **Final Commit**: "Session 16: Final demo and project submission"
- **Deliverable**: Complete project submission with documentation, live demo, presentation

---

## ⚠️ Critical Warnings & Best Practices

### Data Loss Prevention

**🚨 SAVE YOUR PROGRESS REGULARLY ON GITHUB 🚨**

- Commit at the end of EVERY lab session (4-5 hours)
- Push to remote repository immediately after committing
- Never rely solely on local copies or lab environment storage
- Lab environments may reset or lose data unexpectedly
- Use descriptive commit messages with session number and summary

### AWS Cost Management

- **Set up billing alerts**: Create CloudWatch alarm when monthly cost > $50
- **Use AWS Free Tier**: t3.micro for master, t3.micro workers initially
- **Stop instances when not working**: Don't leave cluster running 24/7 during development
- **Delete resources during breaks**: Run `pulumi destroy` if pausing project for days
- **Monitor costs daily**: Check AWS Cost Explorer every session
- **Spot instances**: Use for testing to save 60-70% on EC2 costs

### Common Pitfalls to Avoid

1. **Hardcoded secrets**: Never commit AWS credentials, tokens, or passwords to Git
2. **Security group misconfigurations**: Test connectivity before assuming it works
3. **Lambda timeouts**: Start with 60s, increase if needed, but optimize code first
4. **DynamoDB lock deadlocks**: Always implement lock expiry and cleanup logic
5. **Prometheus connectivity**: Ensure Lambda in same VPC as cluster
6. **Node drain failures**: Always check drain success before terminating instance
7. **IAM permission errors**: Start with broader permissions, narrow down after testing
8. **Version mismatches**: Pin exact versions in package.json and requirements.txt

### Testing Before Production

- Test all failure scenarios in dev environment first
- Never test scale-down logic on production cluster
- Always have backup plan (manual intervention procedure)
- Document rollback steps for each major change

---

## 📚 Reference Materials

### AWS Documentation

- [EC2 User Data Scripts](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/user-data.html)
- [Lambda Python Best Practices](https://docs.aws.amazon.com/lambda/latest/dg/python-handler.html)
- [DynamoDB Conditional Writes](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.ConditionExpressions.html)
- [Secrets Manager SDK](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/secretsmanager.html)
- [Spot Instance Interruptions](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-interruptions.html)

### K3s & Kubernetes

- [K3s Quick Start](https://docs.k3s.io/quick-start)
- [kubectl Drain](https://kubernetes.io/docs/tasks/administer-cluster/safely-drain-node/)
- [PodDisruptionBudgets](https://kubernetes.io/docs/concepts/workloads/pods/disruptions/)
- [Kube State Metrics](https://github.com/kubernetes/kube-state-metrics)

### Prometheus

- [PromQL Basics](https://prometheus.io/docs/prometheus/latest/querying/basics/)
- [Node Exporter Metrics](https://github.com/prometheus/node_exporter)
- [HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/)

### Tools

- [Pulumi AWS TypeScript](https://www.pulumi.com/registry/packages/aws/)
- [k6 Load Testing](https://k6.io/docs/)
- [Grafana Dashboards](https://grafana.com/docs/grafana/latest/dashboards/)

---

## ✅ Final Checklist

Before submission, ensure:

- [ ] GitHub repo has 15+ commits (one per session)
- [ ] README.md covers all required sections
- [ ] Architecture diagram shows all AWS services
- [ ] Lambda function code is clean, commented, tested
- [ ] Prometheus collecting metrics from all nodes
- [ ] Grafana dashboards visualize cluster health
- [ ] DynamoDB lock mechanism prevents race conditions
- [ ] Graceful drain tested (no pod disruptions)
- [ ] CloudWatch alarms configured for failures
- [ ] Slack notifications working for all event types
- [ ] k6 load test results documented
- [ ] Cost analysis shows 40-50% savings
- [ ] Security: Secrets Manager, IAM roles, encrypted volumes
- [ ] Pulumi IaC defines all infrastructure
- [ ] Demo rehearsed, presentation slides ready
- [ ] All bonus features documented (if implemented)

---

## 🎓 Learning Outcomes

By completing this project, you will master:

1. **Cloud Architecture**: Designing production-grade AWS solutions
2. **Serverless Automation**: Building event-driven Lambda workflows
3. **Kubernetes Operations**: Cluster management, monitoring, scaling
4. **Infrastructure as Code**: Pulumi TypeScript for reproducible deployments
5. **Distributed Systems**: Race condition prevention, state management
6. **Observability**: Metrics, logs, alerts, dashboards
7. **Cost Optimization**: Right-sizing, Spot instances, resource utilization
8. **Security**: IAM, encryption, secret management, least privilege
9. **Testing**: Load testing, failure scenarios, unit/integration tests
10. **Technical Communication**: Documentation, diagrams, presentations

---

## 📞 Support & Resources

**Lab Access**: https://poridhi.io/lab-group-modules/67588457fd07f8bfcaacb940/6766cafbb4d4aa86d481b1d2

**Demo App**: https://sd1finalproject.lovable.app

**Instructor Office Hours**: [Insert schedule]

**Slack Channel**: #smartscale-autoscaler (for team collaboration)

**GitHub Issues**: Use for bug tracking and questions

---

**"Scale Smart. Automate Everything. Document Clearly."**

_Good luck building a production-grade autoscaling system!_ 🚀
