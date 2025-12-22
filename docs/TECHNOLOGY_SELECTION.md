# 🎯 Technology Selection & Architecture Decisions

**Project**: SmartScale K3s Autoscaler  
**Date**: December 22, 2025  
**Status**: Planning & Design Phase

---

## 📋 Requirements Analysis Summary

Based on Poridhi.io final exam requirements, we need:

1. ✅ Intelligent metric collection (Prometheus)
2. ✅ Smart scaling logic (Lambda-based)
3. ✅ Automated node provisioning (EC2 API)
4. ✅ Graceful deprovisioning (kubectl drain)
5. ✅ State management & race prevention (DynamoDB)
6. ✅ Monitoring & alerting (CloudWatch + Slack)
7. ✅ Security compliance (Secrets Manager, IAM)
8. ✅ Cost optimization (40-50% reduction)
9. ✅ Infrastructure as Code (Pulumi vs Terraform)

---

## 🔧 Technology Stack Decisions

### 1. Infrastructure as Code (IaC)

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **Pulumi (Python)** | ✅ Same language as Lambda (Python)<br>✅ Type safety & IDE autocomplete<br>✅ Easier testing with pytest<br>✅ Better for complex logic | ⚠️ Smaller community<br>⚠️ Less enterprise adoption | **✅ SELECTED** |
| **Terraform (HCL)** | ✅ Industry standard<br>✅ Massive module library<br>✅ Better state management | ❌ Different language from Lambda<br>❌ HCL less expressive for logic | ❌ Rejected |

**Justification**: Python consistency across Lambda + Pulumi reduces context switching. Type safety catches errors before deployment. Team already knows Python.

---

### 2. Autoscaler Implementation

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **AWS Lambda** | ✅ Serverless (no servers to manage)<br>✅ Pay per invocation (~$0.20/million)<br>✅ Auto-scales itself<br>✅ Easy EventBridge integration | ⚠️ 15-minute max timeout<br>⚠️ Cold start latency | **✅ SELECTED** |
| **ECS Fargate Container** | ✅ No timeout limit<br>✅ Can run continuously | ❌ Always-on cost (~$15/month)<br>❌ Need to manage container lifecycle | ❌ Rejected |
| **EC2 Instance** | ✅ Full control | ❌ High cost ($10-30/month)<br>❌ Single point of failure<br>❌ Need to manage OS | ❌ Rejected |
| **Kubernetes CronJob** | ✅ Native to K8s | ❌ Runs inside cluster being scaled<br>❌ Circular dependency problem | ❌ Rejected |

**Justification**: Lambda is cost-effective ($3-5/month for 15,000 invocations), reliable, and aligns with serverless best practices. 60-second timeout sufficient for our scaling logic.

---

### 3. Metrics Collection

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **Prometheus** | ✅ K8s industry standard<br>✅ Powerful PromQL queries<br>✅ Pull-based (reliable)<br>✅ Free & open source | ⚠️ Need to expose endpoint<br>⚠️ Storage management | **✅ SELECTED** |
| **CloudWatch Container Insights** | ✅ AWS-native<br>✅ No installation | ❌ Limited K3s support<br>❌ Expensive ($7-10/month)<br>❌ Less flexible queries | ❌ Rejected |
| **Datadog** | ✅ Great UI<br>✅ Advanced features | ❌ Very expensive ($15-31/host/month)<br>❌ Overkill for project | ❌ Rejected |

**Justification**: Prometheus is free, K8s-native, and provides exactly the metrics we need. PromQL is powerful for custom scaling logic.

**Exposure Strategy**: NodePort (port 30090) + Security Group restricting Lambda's VPC only. No public internet access. No auth needed (network-level security).

---

### 4. State Management & Locking

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **DynamoDB** | ✅ Conditional writes (atomic locking)<br>✅ Serverless (auto-scales)<br>✅ Fast (<10ms latency)<br>✅ Pay per request | ⚠️ Need to design schema carefully | **✅ SELECTED** |
| **S3 + Object Lock** | ✅ Simple | ❌ Eventual consistency issues<br>❌ No atomic operations<br>❌ Slower | ❌ Rejected |
| **ElastiCache Redis** | ✅ Very fast<br>✅ Native locking | ❌ Always-on cost ($15-50/month)<br>❌ Overkill for our needs | ❌ Rejected |

**Justification**: DynamoDB's conditional writes (`ConditionExpression`) provide atomic distributed locking. Pay-per-request pricing is cost-effective for our infrequent writes (1 write/2 minutes).

**Schema Design**:
```python
{
  "cluster_id": "k3s-techflow-prod",  # Partition key
  "node_count": 5,
  "last_scale_time": "2025-12-22T10:30:00Z",
  "last_scale_action": "scale_up",
  "scaling_in_progress": false,
  "lock_holder": null,
  "lock_expiry": null,
  "last_cpu_avg": 72.5,
  "last_memory_avg": 68.2,
  "last_pending_pods": 3
}
```

---

### 5. Secret Management

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **AWS Secrets Manager** | ✅ Automatic rotation support<br>✅ Encryption at rest<br>✅ Versioning<br>✅ Audit logs | ⚠️ $0.40/secret/month | **✅ SELECTED** |
| **AWS Systems Manager Parameter Store** | ✅ Free for standard params<br>✅ Simple API | ❌ No automatic rotation<br>❌ Limited features | ⚠️ Acceptable alternative |
| **S3 with encryption** | ✅ Cheap | ❌ Not designed for secrets<br>❌ No rotation<br>❌ Manual key management | ❌ Rejected |

**Justification**: Secrets Manager is purpose-built for secrets. K3s join token is critical - worth $0.40/month for proper security, rotation, and audit trail.

**Stored Secrets**:
- `k3s/join-token` - K3s cluster join token
- `k3s/master-ip` - Master node private IP (if using static IP)

---

### 6. Monitoring & Alerting

| Component | Technology | Justification |
|-----------|-----------|---------------|
| **Cluster Metrics** | Prometheus + node_exporter | K8s standard, already decided |
| **Lambda Logs** | CloudWatch Logs | AWS-native, automatic |
| **Custom Metrics** | CloudWatch Metrics (boto3) | Track scaling events, node count |
| **Dashboards** | CloudWatch Dashboards | Free, AWS-native, sufficient for MVP |
| **Advanced Dashboards** | Grafana (optional) | Better visualization, but requires hosting |
| **Alerts** | CloudWatch Alarms + SNS | Free tier: 10 alarms, simple setup |
| **Notifications** | Slack Webhooks (via SNS) | Better UX than email, real-time visibility |

**CloudWatch Metrics to Create**:
- `AutoscalerInvocations` (count)
- `ScaleUpEvents` (count, dimension: reason)
- `ScaleDownEvents` (count)
- `ScalingFailures` (count, dimension: error_type)
- `CurrentNodeCount` (gauge)
- `NodeJoinLatency` (milliseconds)
- `LambdaExecutionTime` (milliseconds)

**CloudWatch Alarms**:
1. `ScalingFailure` - 3 failures in 15 minutes → SNS → Slack
2. `ClusterCPUCritical` - CPU > 90% for 5 minutes → urgent alert
3. `MaxCapacityReached` - node_count = 10 for 10 minutes → warning
4. `NodeJoinFailure` - join latency > 5 minutes → alert

---

### 7. Container Orchestration

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **K3s** | ✅ Lightweight (40 MB vs 400 MB)<br>✅ Single binary<br>✅ Perfect for small clusters<br>✅ Built-in components | ⚠️ Less production adoption | **✅ SELECTED** |
| **K8s (kubeadm)** | ✅ Industry standard | ❌ Complex setup<br>❌ Resource-heavy<br>❌ Overkill for project | ❌ Rejected |
| **EKS** | ✅ Managed | ❌ $73/month just for control plane<br>❌ Defeats purpose of cost optimization | ❌ Rejected |

**Justification**: Requirements explicitly specify K3s. Perfect for lightweight, cost-effective Kubernetes.

---

### 8. Load Testing

| Tool | Pros | Cons | Decision |
|------|------|------|----------|
| **k6** | ✅ Modern, JS-based<br>✅ Great reporting<br>✅ Cloud integration option | ⚠️ Need to learn JS | **✅ SELECTED** |
| **Locust** | ✅ Python-based<br>✅ Distributed load | ⚠️ UI can be unstable | ⚠️ Backup option |
| **Apache Bench** | ✅ Simple | ❌ Limited features<br>❌ No scripting | ❌ Too basic |

**Justification**: k6 is modern, has excellent reporting, and can simulate realistic user patterns. Python alternative (Locust) available if needed.

---

### 9. CI/CD & GitOps (Optional Bonus)

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **FluxCD** | ✅ Lightweight<br>✅ GitOps native | ⚠️ Newer project | ⚠️ Bonus feature |
| **ArgoCD** | ✅ Great UI<br>✅ Mature | ⚠️ More resource-heavy | ⚠️ Bonus feature |
| **Manual deployment** | ✅ Simple for MVP | ❌ Not production-ready | **✅ Phase 1 approach** |

**Justification**: Focus on core autoscaling first. Add GitOps in Phase 4 if time permits.

---

### 10. Cost Optimization Strategies

| Strategy | Technology | Cost Impact | Priority |
|----------|-----------|-------------|----------|
| **Dynamic scaling** | Lambda + EC2 API | **-50% infra cost** | ✅ Core |
| **Spot instances** | EC2 Spot + interruption handling | **-60-70% instance cost** | ⚠️ Bonus (Phase 4) |
| **Multi-AZ balancing** | Custom logic in Lambda | Resilience, not cost | ⚠️ Bonus (Phase 4) |
| **Predictive scaling** | Historical data analysis | **-10-15% additional** | ⚠️ Bonus (Phase 4) |
| **Right-sizing instances** | t3.small → t3.micro for off-peak | **-20% per node** | ✅ Phase 2 |

**Cost Analysis** (monthly, Bangladesh pricing):
- **Before**: 5 × t3.small × 24/7 = ~1.2 lakh BDT
- **After** (dynamic scaling):
  - Off-peak (12h): 2 × t3.small = ~24,000 BDT
  - Peak (12h): 7 × t3.small = ~42,000 BDT
  - Lambda: ~300 BDT
  - DynamoDB: ~100 BDT
  - **Total**: ~66,400 BDT (**45% savings** = ~53,600 BDT/month)

---

## 🏗️ Architecture Components Map

```
Component               Technology          Why This Choice
────────────────────────────────────────────────────────────────
IaC                    Pulumi (Python)     Same language as Lambda
Autoscaler             AWS Lambda          Serverless, cost-effective
Trigger                EventBridge         2-minute interval, reliable
Metrics                Prometheus          K8s standard, powerful PromQL
State/Locking          DynamoDB            Atomic conditional writes
Secrets                Secrets Manager     Secure, auditable, rotation
Cluster                K3s                 Lightweight Kubernetes
Nodes                  EC2 t3.small        Balanced CPU/memory/cost
Monitoring             CloudWatch          AWS-native, free tier
Alerting               CloudWatch Alarms   Simple, integrated with SNS
Notifications          SNS → Slack         Real-time team visibility
Load Testing           k6                  Modern, JS-based, great reports
Dashboards             CloudWatch          Free, sufficient for MVP
Optional: Advanced UI  Grafana             Better viz (Phase 3/4)
Optional: GitOps       FluxCD              Config versioning (Phase 4)
```

---

## 🔒 Security Architecture

| Requirement | Implementation | Technology |
|-------------|----------------|------------|
| **No hardcoded credentials** | IAM roles everywhere | AWS IAM |
| **K3s token security** | Encrypted storage | Secrets Manager |
| **Lambda permissions** | Least-privilege policy | IAM Policy (JSON) |
| **Prometheus access** | Security group + NodePort | AWS VPC |
| **EC2 volume encryption** | EBS encryption at rest | AWS KMS |
| **Inter-node comms** | K3s built-in TLS | K3s default |
| **Audit logging** | All API calls logged | CloudTrail |

---

## 📊 Component Interaction Flow

```
┌─────────────────────────────────────────────────────────────┐
│  STEP 1: Trigger (Every 2 minutes)                          │
│  EventBridge Rule → Lambda Invocation                       │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 2: Lock Check (Race condition prevention)             │
│  Lambda → DynamoDB.get_item(cluster_id)                     │
│  Check: scaling_in_progress == false                        │
│  IF locked: exit gracefully, retry next invocation          │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 3: Metrics Collection                                 │
│  Lambda → Prometheus HTTP API (NodePort 30090)              │
│  Query: avg CPU, avg Memory, pending pods, current nodes    │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 4: Scaling Decision                                   │
│  Python logic evaluates:                                    │
│  - Scale UP if: CPU>70% OR pending_pods>0 OR memory>75%     │
│  - Scale DOWN if: CPU<30% AND pending_pods=0 AND memory<50% │
│  - NO ACTION if: within normal range or cooldown active     │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 5a: Scale UP (if needed)                              │
│  Lambda → DynamoDB.put_item(scaling_in_progress=true)       │
│  Lambda → Secrets Manager.get_secret(k3s-token)             │
│  Lambda → EC2.run_instances(user_data=worker-script)        │
│  Wait for instance "running"                                │
│  Poll kubectl: wait for node "Ready" (max 5 min)            │
│  Lambda → DynamoDB.update(node_count++, lock=false)         │
│  Lambda → SNS.publish("Scale-up: +2 nodes, CPU was 78%")    │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 5b: Scale DOWN (if needed)                            │
│  Lambda → DynamoDB.put_item(scaling_in_progress=true)       │
│  Identify least-utilized node (fewest pods)                 │
│  Lambda → kubectl cordon <node>                             │
│  Lambda → kubectl drain <node> --timeout=5m                 │
│  Wait for drain completion                                  │
│  Lambda → EC2.terminate_instances(instance_id)              │
│  Lambda → DynamoDB.update(node_count--, lock=false)         │
│  Lambda → SNS.publish("Scale-down: -1 node, CPU was 25%")   │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  STEP 6: Logging & Metrics                                  │
│  Lambda → CloudWatch Logs (decision rationale)              │
│  Lambda → CloudWatch Metrics (custom metrics)               │
│  SNS → Slack Webhook (team notification)                    │
└─────────────────────────────────────────────────────────────┘
```

---

## ✅ Technology Selection Summary

**Selected Stack**:
- ☁️ **Cloud**: AWS (EC2, Lambda, DynamoDB, Secrets Manager, CloudWatch)
- 🏗️ **IaC**: Pulumi (Python)
- 🤖 **Autoscaler**: AWS Lambda (Python 3.11, EventBridge trigger)
- 📊 **Metrics**: Prometheus (NodePort exposure)
- 🗄️ **State**: DynamoDB (conditional writes)
- 🔐 **Secrets**: AWS Secrets Manager
- ☸️ **Cluster**: K3s (lightweight Kubernetes)
- 💻 **Nodes**: EC2 t3.small (Ubuntu 22.04 LTS)
- 📈 **Monitoring**: CloudWatch + Grafana (optional)
- 🔔 **Alerts**: CloudWatch Alarms + SNS + Slack
- 🧪 **Load Testing**: k6

**Why This Stack Wins**:
1. ✅ Meets all Poridhi.io requirements (100% coverage)
2. ✅ Cost-effective (45-50% infrastructure savings)
3. ✅ Python throughout (Lambda + Pulumi + testing)
4. ✅ Serverless where possible (Lambda > always-on)
5. ✅ AWS-native services (easier integration)
6. ✅ Production-ready security (Secrets Manager, IAM roles)
7. ✅ Scalable architecture (no bottlenecks)
8. ✅ Well-documented ecosystem (Prometheus, K3s, boto3)

---

## 🎓 Learning & Skill Development

This stack teaches:
- ✅ Serverless architectures (Lambda)
- ✅ Infrastructure as Code (Pulumi)
- ✅ Kubernetes fundamentals (K3s)
- ✅ Metrics & observability (Prometheus, CloudWatch)
- ✅ Distributed systems (locking, race conditions)
- ✅ AWS services (10+ services integrated)
- ✅ Python best practices (boto3, error handling)
- ✅ Security engineering (IAM, Secrets Manager)

---

## 📝 Next Step: Implementation Phases

See [IMPLEMENTATION_PHASES.md](./IMPLEMENTATION_PHASES.md) for detailed 4-phase rollout plan.
