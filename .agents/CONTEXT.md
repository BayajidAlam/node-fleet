# node-fleet K3s Autoscaler — Project Context

> **This is the single source of truth for the entire project.**
> Every agent, skill, and AI conversation should read this file first.
> File location: `.agents/CONTEXT.md`

---

## 🎯 What This Project Is

**SmartScale K3s Autoscaler** — a production-grade intelligent autoscaling system for K3s (lightweight Kubernetes) on AWS EC2. Built for TechFlow Solutions (e-commerce, 15,000+ daily users, ~80 lakh BDT/month transactions).

**Problem Solved:**

- Eliminates 60,000 BDT/month waste during off-peak hours (9PM–9AM, CPU 20-30%)
- Prevents outages during peak/flash sales (CPU 85-95%)
- Replaces 15-20 min manual scaling with <3 min automated response

**Current Status:** Fully implemented. All core requirements and all 7 bonus challenges complete.

**Repository:** https://github.com/BayajidAlam/node-fleet | **Branch:** main

---

## 🏗️ Architecture at a Glance

```
EventBridge (every 2 min)
    │
    ▼
AWS Lambda (Python 3.11, 256MB, 60s timeout)
    │
    ├─── Secrets Manager ──→ Prometheus credentials
    │
    ├─── Prometheus NodePort:30090 ──→ CPU / Memory / Pending Pods
    │         (inside K3s cluster, basic auth)
    │
    ├─── DynamoDB ──→ acquire lock, read state + history
    │
    ├─── Scaling Decision Engine
    │         │
    │         ├── scale_up  ──→ EC2 RunInstances (on-demand or spot)
    │         │                  Worker UserData ──→ Secrets Manager (K3s token)
    │         │                  SSH to master ──→ wait for "Ready"
    │         │
    │         └── scale_down ──→ SSH to master (kubectl drain, 300s timeout)
    │                            EC2 TerminateInstances
    │                            kubectl delete node (remove ghost)
    │
    ├─── DynamoDB ──→ update state, release lock
    ├─── CloudWatch ──→ metrics + logs
    └─── SNS ──→ Slack webhook (scale event notification)
```

### Data Flow (End-to-End)

```
1. EventBridge fires every 2 minutes
2. Lambda checks DynamoDB lock → exits if scaling in progress
3. Lambda fetches Prometheus credentials from Secrets Manager
4. Lambda queries Prometheus /api/v1/query for cluster metrics
5. Scaling decision evaluated with 5-reading history from DynamoDB
6. If scale_up:  acquire lock → RunInstances → worker joins via UserData → wait Ready
   If scale_down: acquire lock → SSH drain → TerminateInstances → delete node
7. DynamoDB state updated, lock released in `finally` block
8. CloudWatch metrics published, Slack notification sent via SNS
```

---

## 📁 Actual Folder Structure

```
node-fleet/
├── .github/
│   └── copilot-instructions.md    ← Global Copilot context
├── .agents/
│   ├── CONTEXT.md                 ← THIS FILE (source of truth)
│   ├── app-agent.agent.md         ← Lambda/Python code agent
│   ├── infra-agent.agent.md       ← Pulumi TypeScript IaC agent
│   ├── review-agent.agent.md      ← Code review agent
│   ├── docs-agent.agent.md        ← Documentation agent
│   └── skills/                    ← Skill modules
├── lambda/                        ← Python 3.11 Lambda code
│   ├── autoscaler.py              ← Main handler (6-step flow)
│   ├── scaling_decision.py        ← All thresholds and scaling logic
│   ├── metrics_collector.py       ← Prometheus PromQL queries
│   ├── ec2_manager.py             ← EC2 launch/terminate/drain via SSH
│   ├── state_manager.py           ← DynamoDB lock + state management
│   ├── slack_notifier.py          ← SNS → Slack webhook
│   ├── multi_az_helper.py         ← Multi-AZ subnet balancing
│   ├── spot_instance_helper.py    ← 70% Spot mix, interruption handling
│   ├── predictive_scaling.py      ← 7-day historical pattern analysis
│   ├── custom_metrics.py          ← Queue depth, latency p95, error rate
│   ├── cost_optimizer.py          ← Instance hours, cost tracking
│   ├── dynamic_scheduler.py       ← Dynamic EventBridge interval adjustment
│   ├── audit_logger.py            ← DynamoDB streams audit trail
│   └── requirements.txt
├── pulumi/                        ← TypeScript IaC (NOT Python)
│   └── src/
│       ├── vpc.ts                 ← VPC, 2 public + 2 private subnets
│       ├── ec2-master.ts          ← Master node (t3.medium)
│       ├── ec2-worker.ts          ← Worker launch templates (on-demand + spot)
│       ├── lambda.ts              ← Lambda + EventBridge + SQS DLQ
│       ├── dynamodb.ts            ← State table + metrics history table
│       ├── iam.ts                 ← Least-privilege Lambda IAM role
│       ├── security-groups.ts     ← SGs for master, workers, Lambda
│       ├── cloudwatch-alarms.ts   ← All CloudWatch alarms
│       ├── sns.ts                 ← SNS topic + Slack notifier Lambda
│       ├── secrets.ts             ← Secrets Manager resources
│       ├── s3.ts                  ← Lambda artifacts bucket
│       └── keypair.ts             ← EC2 key pair
├── k3s/
│   ├── master-setup.sh            ← K3s master init, Prometheus, basic auth
│   └── worker-userdata.sh         ← Auto-join via Secrets Manager token
├── gitops/
│   └── infrastructure/
│       └── prometheus-deployment.yaml  ← K8s manifest (retention: 7d, scrape: 15s)
├── monitoring/
│   └── grafana-dashboards/        ← Grafana dashboard JSON
├── demo-app/                      ← Flask load-test app + K8s manifests
├── tests/
│   └── lambda/                    ← 120+ pytest unit + integration tests
├── docs/                          ← Architecture diagrams, runbooks
│   ├── ARCHITECTURE.md
│   ├── SCALING_ALGORITHM.md
│   ├── DEPLOYMENT_GUIDE.md
│   ├── COST_ANALYSIS.md
│   ├── SECURITY_CHECKLIST.md
│   ├── TESTING.md
│   └── TROUBLESHOOTING.md
└── scripts/                       ← Utility scripts
```

---

## ⚙️ Scaling Rules (Canonical — All Enforced in `lambda/scaling_decision.py`)

### Scale UP when ANY is true:

| Condition    | Threshold      | Variable                           | Window                                 |
| ------------ | -------------- | ---------------------------------- | -------------------------------------- |
| Average CPU  | > 70%          | `CPU_SCALE_UP_THRESHOLD = 70.0`    | `window=3` (3 readings × 2min = ~6min) |
| Pending pods | > 0 for > 3min | pending > 0                        | `window=2` (2 readings × 2min = ~4min) |
| Memory       | > 75%          | `MEMORY_SCALE_UP_THRESHOLD = 75.0` | `window=3`                             |

### Scale DOWN when ALL are true:

| Condition       | Threshold | Variable                             | Window                        |
| --------------- | --------- | ------------------------------------ | ----------------------------- |
| Average CPU     | < 30%     | `CPU_SCALE_DOWN_THRESHOLD = 30.0`    | `window=5` (5 × 2min = 10min) |
| No pending pods | 0         | < 1                                  | `window=5`                    |
| Memory          | < 50%     | `MEMORY_SCALE_DOWN_THRESHOLD = 50.0` | `window=5`                    |

### Constraints:

- Min nodes: **2** | Max nodes: **10**
- Scale-up: add 1 node (2 if CPU > 85% OR pending > 5)
- Scale-down: remove 1 node at a time
- Cooldown after scale-up: **5 min** (`SCALE_UP_COOLDOWN = 300`)
- Cooldown after scale-down: **10 min** (`SCALE_DOWN_COOLDOWN = 600`)

---

## ⚙️ Lambda Specification

| Setting | Value                                                      |
| ------- | ---------------------------------------------------------- |
| Runtime | Python 3.11                                                |
| Memory  | 256 MB                                                     |
| Timeout | 60 seconds                                                 |
| Trigger | EventBridge `rate(2 minutes)`                              |
| DLQ     | SQS `node-fleet-cluster-autoscaler-dlq` (14-day retention) |

**6-Step Lambda Flow:**

1. Check DynamoDB lock — exit if `scaling_in_progress = true`
2. Fetch Prometheus credentials from Secrets Manager
3. Query Prometheus /api/v1/query (CPU, memory, pending pods)
4. Evaluate scaling conditions with history
5. If action: acquire lock → EC2 API calls → update state
6. Release lock in `finally` block (always, even on failure)

---

## ⚙️ DynamoDB Schema

**Table:** `k3s-autoscaler-state` (or env var `STATE_TABLE`)

- Partition key: `cluster_id` (String)
- Attributes: `node_count`, `last_scale_time`, `scaling_in_progress`, `lock_acquired_at`, `lock_expiry`
- Lock expiry: **360 seconds** (covers worst-case drain 300s + node join)
- Stale lock auto-release: `lock_acquired_at < now - 360`

---

## ⚙️ Prometheus Configuration

| Setting         | Value                                        |
| --------------- | -------------------------------------------- |
| Scrape interval | 15 seconds                                   |
| Retention       | **7 days** (NOT 30d)                         |
| Exposure        | NodePort 30090, basic auth                   |
| Credentials     | Secrets Manager `node-fleet/prometheus-auth` |

**PromQL Queries Used:**

```promql
CPU:     avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) * 100
Memory:  (1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100
Pending: sum(kube_pod_status_phase{phase="Pending"})
Nodes:   count(kube_node_info)
```

---

## ⚙️ Secrets Manager Keys

| Secret                       | Content                                  | Used By                |
| ---------------------------- | ---------------------------------------- | ---------------------- |
| `node-fleet/k3s-token`       | K3s join token                           | worker UserData script |
| `node-fleet/prometheus-auth` | `{"username": "...", "password": "..."}` | Lambda autoscaler      |
| `node-fleet/ssh-key`         | RSA private key PEM                      | Lambda SSH to master   |
| `node-fleet/slack-webhook`   | Slack webhook URL                        | Slack notifier Lambda  |

---

## ⚙️ Graceful Scale-Down (Strict Order)

1. Select safest node via weighted score (fewest pods, no critical/StatefulSet/single-replica)
2. SSH to master → `kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --force --timeout=300s`
   - `kubectl drain` implicitly cordons first — no separate cordon step needed
3. Validate: exit code = 0 AND `"drained"` in output — skip termination if failed
4. `ec2.terminate_instances(InstanceIds=[instance_id])`
5. SSH to master → `kubectl delete node <node>` (removes ghost node)

**Never terminate nodes hosting:**

- `kube-system` pods (CoreDNS, metrics-server, etc.)
- StatefulSet pods
- Single-replica deployments

---

## ⚙️ Master IP Resolution

**NEVER hardcode the master IP.** Always use `_get_master_ip()`:

```python
response = self.ec2_client.describe_instances(
    Filters=[
        {'Name': 'tag:Role', 'Values': ['k3s-master']},
        {'Name': 'instance-state-name', 'Values': ['running']}
    ]
)
return response['Reservations'][0]['Instances'][0]['PrivateIpAddress']
```

---

## ⚙️ Prometheus Credentials Pattern

**NEVER use hardcoded fallback passwords.** Fail-fast if unavailable:

```python
def get_prometheus_credentials():
    try:  # Secrets Manager first
        sm = boto3.client('secretsmanager')
        creds = json.loads(sm.get_secret_value(SecretId="node-fleet/prometheus-auth")['SecretString'])
        return creds['username'], creds['password']
    except Exception:  # Fallback to env vars
        u, p = os.environ.get("PROMETHEUS_USERNAME"), os.environ.get("PROMETHEUS_PASSWORD")
        if not u or not p:
            raise ValueError("Prometheus credentials unavailable")
        return u, p
```

---

## ⚙️ CloudWatch Alarms (in `pulumi/src/cloudwatch-alarms.ts`)

| Alarm             | Condition                       | Action               |
| ----------------- | ------------------------------- | -------------------- |
| Scaling failures  | Lambda errors                   | SNS notification     |
| CPU emergency     | > 90% for 5 min                 | SNS urgent alert     |
| Max capacity      | Node count = 10 for 10+ min     | SNS capacity warning |
| Node join failure | Join latency metric > threshold | SNS alert            |

---

## 🌟 Bonus Features — All Implemented

| Bonus                | Module                         | Notes                                   |
| -------------------- | ------------------------------ | --------------------------------------- |
| Multi-AZ workers     | `multi_az_helper.py`           | Balances across ap-southeast-1a/1b      |
| Spot instances (70%) | `spot_instance_helper.py`      | Interruption drain + On-Demand fallback |
| Predictive scaling   | `predictive_scaling.py`        | 7-day history, pre-scales 10min early   |
| Custom app metrics   | `custom_metrics.py`            | Queue depth, latency p95, error rate    |
| GitOps               | `gitops/`                      | Versioned K8s manifests                 |
| Slack notifications  | `slack_notifier.py` + `sns.ts` | Scale up/down/fail/warning              |
| Cost dashboard       | `cost_optimizer.py`            | Instance hours, savings %, Lambda cost  |

---

## 🤖 Agent Strategy

Use **3 focused agents** — don't mix concerns:

| Agent          | Scope                                                     | Skills                                                    |
| -------------- | --------------------------------------------------------- | --------------------------------------------------------- |
| `app-agent`    | `lambda/`, `demo-app/`, `tests/` — Python code            | `code-reviewer`, `aws-solution-architect`                 |
| `infra-agent`  | `pulumi/`, `k3s/`, `gitops/`, `monitoring/` — IaC + infra | `pulumi-best-practices`, `aws-solution-architect`         |
| `review-agent` | All code — read-only review + architecture analysis       | `code-reviewer`, `aws-solution-architect`, `aws-diagrams` |
| `docs-agent`   | `docs/`, `README.md` — documentation + diagrams           | `documentation-authoring`, `aws-diagrams`                 |

---

## 🔧 Development Commands

```bash
# Infrastructure
cd pulumi && pulumi up
pulumi stack output masterIp

# Lambda packaging
cd lambda
pip install -r requirements.txt -t .
zip -r function.zip . --exclude "*.pyc" "venv/*" "tests/*"
aws lambda update-function-code --function-name node-fleet-cluster-autoscaler --zip-file fileb://function.zip

# K3s cluster
ssh -i node-fleet-key.pem ubuntu@<master-ip>
./k3s/master-setup.sh
kubectl get nodes
kubectl apply -f gitops/infrastructure/prometheus-deployment.yaml

# Testing
cd tests && python -m pytest lambda/ -v
k6 run demo-app/load-test.js --vus 100 --duration 5m
```

---

## 📌 Known Gotchas

1. **Pulumi is TypeScript** — all IaC is `.ts` files in `pulumi/src/`. Never write `.py` in `pulumi/`.
2. **Master IP is dynamic** — always use `_get_master_ip()` EC2 tag lookup.
3. **Prometheus retention is 7d** — `gitops/infrastructure/prometheus-deployment.yaml` must use `--storage.tsdb.retention.time=7d`.
4. **DLQ requires `sqs:SendMessage` IAM permission** on the Lambda role (`pulumi/src/iam.ts`).
5. **Window math**: `window=5` at 2-min intervals = 10 min. `window=2` = ~4 min (closest to 3-min spec).
6. **Lock expiry is 360s** — not 120s. Covers worst-case drain (300s) + join.
7. **Drain validation**: check both exit code AND `"drained"` in kubectl output. Exit 0 alone is not enough.
8. **Spot drain timeout** must be 300s (consistent with normal scale-down). Not 120s.

---

## 🎯 What This Project Is

**VisionSync** is a production-ready, cloud-native **video streaming platform** built on AWS.

Users upload videos → they are automatically transcoded into adaptive DASH format → delivered globally via CloudFront CDN with real-time status updates.

---

## 📊 Documentation & Diagrams (Excalidraw)

**Rule:** Whenever an agent explains a data flow, process, architecture, or complex logic, it **MUST** generate a raw `.excalidraw` JSON block.
_Why?_ The user requires diagrams to be directly copy-pasteable as Excalidraw files for visual documentation. **DO NOT** use Mermaid. Always output Excalidraw JSON in a code block.

---

## 🏗️ Architecture at a Glance

```
User Browser
    │
    ├─── HTTPS ──→ CloudFront CDN (global delivery of processed videos)
    │
    └─── HTTP/WS → ALB (Application Load Balancer)
                       │
               Backend EC2 ASG (1–5 instances, private subnet)
               Node.js + Express + Socket.IO  [port 5000]
                  │          │
              MongoDB RS   Redis EC2
              (3 nodes)   (1 node)
              zone 1c     zone 1c

S3 Raw Bucket ──(Event Notification)──→ SQS Queue → Lambda Fn
                                                          │
                                                   ECS Fargate Task
                                                   (FFmpeg container)
                                                          │
                                                   S3 Processed Bucket
                                                          │
                                                   CloudFront CDN ──→ User (DASH.js player)
```

### Data Flow (End-to-End)

```
1.  User requests presigned URL  → POST /api/upload/generate-presigned-url
                                   Backend creates MongoDB record (status: UPLOADING)
                                   Returns presigned URL + videoId to client
2.  User uploads directly to S3  → S3 raw bucket (no backend involved in transfer)
3.  S3 fires Event Notification  → SQS queue automatically (upload complete trigger)
4.  Lambda triggered by SQS      → reads videoId + fileSize, launches ECS Fargate task
                                   Spot (70%) if file < 1GB, Regular (30%) otherwise
                                   If Spot unavailable → auto-retry on Regular
5.  ECS container (FFmpeg)       → downloads from S3 raw, transcodes all resolutions
                                   Regular: 1080p/720p/480p/360p | Spot: 720p/480p/360p
6.  ECS uploads chunks + MPD     → S3 processed bucket
7.  ECS sends webhook            → POST /api/webhook/processing-complete
8.  Backend updates MongoDB      → status: READY, stores CloudFront manifest URL
9.  Backend emits Socket.IO      → to videoId room → Frontend receives event
10. Frontend plays video         → DASH.js streams from CloudFront URL
```

---

## 📁 Folder Structure

| Folder       | Purpose                           | Key Tech                                    |
| ------------ | --------------------------------- | ------------------------------------------- |
| `server/`    | Backend API + WebSocket server    | Node.js, Express, TypeScript, Socket.IO     |
| `client/`    | Frontend SPA                      | React, TypeScript, Vite, DASH.js, Shadcn UI |
| `container/` | Video transcoding worker          | FFmpeg, Node.js, AWS SDK v3                 |
| `lambda/`    | ECS task orchestrator             | Node.js, AWS SDK v3                         |
| `IaC/`       | Cloud infrastructure              | Pulumi, TypeScript                          |
| `ansible/`   | Server configuration & deployment | Ansible, Jinja2                             |
| `Makefile`   | Deployment automation             | GNU Make                                    |
| `doc/`       | Architecture documentation        | Markdown                                    |

---

## ⚙️ Environment Variables (Canonical List)

All from `server/src/config/env.ts`. **Required** vars (app won't start without these):

```bash
# AWS Core
AWS_REGION=ap-southeast-1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

# S3
S3_BUCKET_RAW=           # Landing zone for user uploads
S3_BUCKET_PROCESSED=     # FFmpeg output: chunks + manifest.mpd

# Messaging
SQS_QUEUE_URL=           # Video processing queue

# Database
MONGODB_URI=             # Replica set URI: mongodb://ip1,ip2,ip3/vision-sync?replicaSet=rs0

# Optional (have defaults)
REDIS_URL=redis://localhost:6379
REDIS_HOST=localhost
REDIS_PORT=6379
CLOUDFRONT_DOMAIN=       # cdn.example.com — serves processed videos
CLOUDFRONT_DISTRIBUTION_ID=
ECS_CLUSTER_NAME=
ECS_TASK_DEFINITION=
PORT=5000
NODE_ENV=development
FRONTEND_URL=http://localhost:3000

# Rate Limiting
RATE_LIMIT_WINDOW_MS=900000   # 15 min
RATE_LIMIT_MAX_REQUESTS=100
UPLOAD_RATE_LIMIT_MAX=5       # Max 5 uploads per window

# ECS / Video Processing
ECS_USE_FARGATE_SPOT=true
ECS_SPOT_PERCENTAGE=70        # 70% Spot, 30% Regular
ECS_TASK_CPU=1024             # 1 vCPU
ECS_TASK_MEMORY=2048          # 2 GB
FFMPEG_PRESET=fast            # fast=Regular, medium=Spot
FFMPEG_THREADS=2
```

---

## 🌐 API Endpoints

### Video Routes (`/api/videos`)

| Method   | Path                     | Description                        |
| -------- | ------------------------ | ---------------------------------- |
| `GET`    | `/`                      | List all videos                    |
| `GET`    | `/:id`                   | Get single video by ID             |
| `GET`    | `/:id/status`            | Get video processing status        |
| `GET`    | `/search?q=`             | Search videos by title/description |
| `GET`    | `/stats/overview`        | Video platform statistics          |
| `GET`    | `/status/:status`        | Filter videos by status            |
| `GET`    | `/:id/manifest.mpd`      | Redirect to DASH manifest on S3    |
| `GET`    | `/:id/segments/:segment` | Redirect to video segment on S3    |
| `PUT`    | `/:id`                   | Update video title/description     |
| `DELETE` | `/:id`                   | Delete video                       |

### Upload Routes (`/api/upload`)

| Method | Path                      | Description                                                                                                                                   |
| ------ | ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST` | `/generate-presigned-url` | Get S3 presigned URL + create MongoDB record (status: UPLOADING)                                                                              |
| `POST` | `/confirm/:id`            | Client calls after S3 upload to update status to UPLOADED (UI feedback only — processing is triggered independently by S3 Event Notification) |

### Webhook Routes (`/api/webhook`)

| Method | Path                   | Description                            |
| ------ | ---------------------- | -------------------------------------- |
| `POST` | `/processing-complete` | ECS container notifies processing done |
| `GET`  | `/health`              | Service health check                   |

### Health

| Method | Path      | Description                        |
| ------ | --------- | ---------------------------------- |
| `GET`  | `/health` | ALB health check — must return 200 |

---

## 🔌 Socket.IO Events

| Event          | Direction       | Payload                                     | When                         |
| -------------- | --------------- | ------------------------------------------- | ---------------------------- |
| `video:status` | Server → Client | `{ videoId, status, manifestUrl?, error? }` | Any status change            |
| `join:video`   | Client → Server | `{ videoId }`                               | Client subscribes to a video |

**Room pattern:** Each video has its own Socket.IO room named by `videoId`.

---

## 🎬 Video Processing Details

| Setting                       | Value                                  |
| ----------------------------- | -------------------------------------- |
| Output format                 | MPEG-DASH (`.mpd` + `.m4s` chunks)     |
| Resolutions (Regular Fargate) | 1080p, 720p, 480p, 360p                |
| Resolutions (Spot Fargate)    | 720p, 480p, 360p                       |
| Segment duration              | 6 seconds                              |
| Audio codec                   | AAC, 128k                              |
| Video codec                   | H.264 (libx264)                        |
| Thumbnail                     | Generated at 1s mark → `thumbnail.jpg` |
| Spot threshold                | Files < 1GB → 70% chance Spot          |

### Video Status Flow

```
UPLOADING → UPLOADED → PROCESSING → READY
                                  ↘ ERROR
```

- `UPLOADING` — set when presigned URL is generated (MongoDB record created)
- `UPLOADED` — set when client calls `POST /confirm/:id` after S3 upload completes (UI feedback)
- `PROCESSING` — set when Lambda launches the ECS task
- `READY` — set by webhook when ECS processing completes successfully
- `ERROR` — set by webhook on ECS failure, or on Spot interruption

> **Note:** `UPLOADED` and processing are decoupled. The client calls `/confirm` to update UI status, while S3 Event Notification independently fires the SQS trigger — so processing starts regardless of whether the client confirms.

### Webhook Payload (ECS → Backend)

```json
{
  "videoId": "string",
  "status": "ready | error",
  "manifestUrl": "https://cdn.example.com/<videoId>/manifest.mpd",
  "error": "optional error message"
}
```

---

## 🏢 AWS Infrastructure

### Regions & AZs

- **Region:** `ap-southeast-1` (Singapore)
- **AZs used:** `ap-southeast-1a`, `ap-southeast-1b`, `ap-southeast-1c`

### Network (VPC CIDR: `10.10.0.0/16`)

| Subnet    | CIDR           | AZ  | Contains                 |
| --------- | -------------- | --- | ------------------------ |
| Public 1  | `10.10.1.0/24` | 1a  | ALB, Bastion             |
| Public 2  | `10.10.2.0/24` | 1b  | ALB                      |
| Private 1 | `10.10.3.0/24` | 1a  | Backend EC2              |
| Private 2 | `10.10.4.0/24` | 1b  | Backend EC2              |
| Private 3 | `10.10.5.0/24` | 1c  | MongoDB (3 nodes), Redis |

### Compute

| Service     | Spec              | Count     | Purpose              |
| ----------- | ----------------- | --------- | -------------------- |
| Backend EC2 | t3.micro          | 1–5 (ASG) | API server           |
| Bastion EC2 | t3.micro          | 1         | SSH gateway          |
| MongoDB EC2 | t3.micro          | 3         | DB replica set       |
| Redis EC2   | t3.micro          | 1         | Cache + rate limiter |
| ECS Fargate | 2 vCPU / 4GB      | dynamic   | Video processing     |
| Lambda      | Node.js 18, 512MB | 1         | ECS launcher         |

### Scaling Rules

- **Backend ASG:** CPU < 10% → scale in, CPU > 80% → scale out, min 1 / max 5
- **ECS:** Scales based on SQS queue depth

### S3 Buckets

| Bucket                  | Purpose                 | Lifecycle                                          |
| ----------------------- | ----------------------- | -------------------------------------------------- |
| `vision-sync-raw`       | Uploaded source videos  | Delete after 7 days                                |
| `vision-sync-processed` | DASH chunks + manifests | Move to S3-IA after 30 days, Glacier after 90 days |

### IAM Role Permissions (Least Privilege)

```
Backend EC2:  s3:GetPresignedUrl (generate presigned URL), s3:GetObject (processed),
              cloudfront:CreateInvalidation
              NOTE: Backend no longer needs sqs:SendMessage — S3 triggers SQS directly

S3 Bucket:    s3:SendMessage to SQS (via S3 Event Notification policy on the SQS queue)

ECS Task:     s3:GetObject (raw bucket), s3:PutObject (processed bucket),
              ecr:GetAuthorizationToken

Lambda:       ecs:RunTask, iam:PassRole,
              sqs:ReceiveMessage, sqs:DeleteMessage, sqs:GetQueueAttributes
```

---

## 🔒 Security Model

- All application instances in **private subnets** — no direct internet access
- **Bastion host** is the only SSH entry point (public subnet)
- SSH pattern: `ssh -J ubuntu@<BASTION_IP> ubuntu@<PRIVATE_IP>`
- SSH key: `~/.ssh/vision-sync-backend`
- Default SSH user: `ubuntu` on all instances
- Security groups: least privilege — specific ports between specific SGs only
- S3 buckets: **no public access** — all video delivery via CloudFront only
- Presigned URLs expire in **15 minutes** (900 seconds)

---

## 🐳 Docker & ECR

| Image                   | ECR Repo | Built from             |
| ----------------------- | -------- | ---------------------- |
| `vision-sync-backend`   | ECR      | `server/Dockerfile`    |
| `vision-sync-frontend`  | ECR      | `client/Dockerfile`    |
| `vision-sync-processor` | ECR      | `container/Dockerfile` |

ECR auth expires every **12 hours** — always run ECR login before pushing/pulling:

```bash
aws ecr get-login-password --region ap-southeast-1 | docker login --username AWS --password-stdin <ECR_URL>
```

---

## 🗄️ Database

### MongoDB

- **Type:** Self-managed replica set on EC2
- **Replica set name:** `rs0`
- **Topology:** 1 Primary + 2 Secondary (all in zone 1c)
- **Port:** `27017`
- **Connection URI pattern:** `mongodb://ip1:27017,ip2:27017,ip3:27017/vision-sync?replicaSet=rs0`
- **Read preference:** `secondaryPreferred`

### Redis

- **Type:** Docker container on EC2 (zone 1c)
- **Port:** `6379`
- **Container name:** `vision-sync-redis`
- **Used for:**
  1. **Rate limiting store** — sliding window / token bucket across all backend instances
  2. **Socket.IO adapter** — backplane for multi-instance event broadcasting
  3. **Response caching** — video list, single video, search results, status-filtered lists, stats
- **Cache invalidation:** Video list + related caches are invalidated on any status change
- **Cache keys used by `cacheService`:** video metadata, video list, search results, stats
- **Known SPOF:** Single node — no replica — if Redis fails, caching degrades gracefully but rate limiting and Socket.IO backplane are affected

---

## 🚀 Deployment Order

```bash
make install          # 1. Install all npm dependencies
make deploy           # 2. Provision AWS infra with Pulumi
make create-inventory # 3. Generate Ansible inventory from Pulumi outputs
make update-env       # 4. Populate server/.env with AWS resource values
make setup-all-db     # 5. Init MongoDB replica set + Redis
make push-containers  # 6. Build + push all Docker images to ECR
make deploy-services  # 7. Deploy containers to EC2 via Ansible
make status           # 8. Verify everything is healthy
```

**Fast update (code change only):**

```bash
make deploy-fast      # Rebuilds images + re-deploys backend only
```

---

## 🛠️ Skills Available

Read the relevant skill BEFORE doing any work in that area:

| Skill                    | When to Use                                         | Path                                             |
| ------------------------ | --------------------------------------------------- | ------------------------------------------------ |
| `aws-solution-architect` | Architecture decisions, HA, cost, service selection | `.agents/skills/aws-solution-architect/SKILL.md` |
| `pulumi-best-practices`  | Writing/reviewing any `IaC/` code                   | `.agents/skills/pulumi-best-practices/SKILL.md`  |
| `ansible-playbooks`      | Writing/debugging any `ansible/` playbook           | `.agents/skills/ansible-playbooks/SKILL.md`      |
| `nodejs-backend`         | Working on `server/` code                           | `.agents/skills/nodejs-backend/SKILL.md`         |
| `ffmpeg-video-pipeline`  | Working on `container/` or `lambda/`                | `.agents/skills/ffmpeg-video-pipeline/SKILL.md`  |
| `makefile-automation`    | Adding/fixing `Makefile` targets                    | `.agents/skills/makefile-automation/SKILL.md`    |
| `code-reviewer`          | Reviewing any code before merge/deploy              | `.agents/skills/code-reviewer/SKILL.md`          |
| `aws-diagrams`           | Generating architecture diagrams                    | `.agents/skills/aws-diagrams/SKILL.md`           |

---

## 🤖 Agent Strategy

Use **3 focused agent contexts** — don't mix concerns in one long session:

| Agent          | Scope                                            | Skills to load                                                                                |
| -------------- | ------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| `infra-agent`  | `IaC/`, `ansible/`, `Makefile`, AWS architecture | `pulumi-best-practices`, `ansible-playbooks`, `makefile-automation`, `aws-solution-architect` |
| `app-agent`    | `server/`, `container/`, `lambda/`, `client/`    | `nodejs-backend`, `ffmpeg-video-pipeline`                                                     |
| `review-agent` | Code review, diagrams, architecture proposals    | `code-reviewer`, `aws-solution-architect`, `aws-diagrams`                                     |

---

## 📌 Key Known Issues / Gotchas

1. **MongoDB single-AZ:** All 3 nodes are in zone 1c — a zone outage takes down the entire DB
2. **Redis SPOF:** No replica — Redis failure breaks rate limiting AND Socket.IO broadcast
3. **Bastion as SSH gateway:** All private instance access goes through bastion — if it's down, no SSH
4. **ECR auth expires:** 12-hour token — always re-login before pushing containers
5. **Ubuntu AMI naming conflict:** Bastion and Backend EC2 have explicit named exports in `IaC/index.ts` to avoid this — don't use `export * from './compute/bastion'`
6. **trust proxy:** Must be set (`app.set('trust proxy', 1)`) for `req.ip` to work correctly behind ALB
7. **Spot interruption:** ECS tasks must handle `SIGTERM` and notify backend before exiting
8. **SQS Queue policy:** Must allow `s3.amazonaws.com` as a principal to call `sqs:SendMessage` — otherwise S3 Event Notification will silently fail
9. **S3 Event Notification setup:** Configure in IaC (`IaC/src/storage/s3.ts`) to fire on `s3:ObjectCreated:*` events for the `videos/` prefix in the raw bucket
