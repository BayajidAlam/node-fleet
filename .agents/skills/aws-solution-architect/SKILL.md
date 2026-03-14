---
name: aws-solution-architect
version: 1.0.0
description: AWS Solution Architect best practices for VisionSync. Covers Well-Architected Framework applied to the platform, multi-AZ design, cost optimization with Spot/Reserved/Savings Plans, security hardening, scalability patterns, and service selection guidance.
---

> 📋 **Always read [`.agents/CONTEXT.md`](./../CONTEXT.md) first** — it contains the full project architecture, env vars, API routes, and infrastructure details for VisionSync.

# AWS Solution Architect Skill

## When to Use This Skill

Invoke this skill when:
- Designing or reviewing new AWS architecture for VisionSync
- Selecting between AWS services (e.g., ECS vs Lambda, SQS vs SNS)
- Optimizing cloud costs (Spot instances, lifecycle policies, Reserved Instances)
- Hardening security posture (IAM, security groups, VPC design)
- Planning for high availability and disaster recovery
- Deciding scaling strategy for new components
- Evaluating tradeoffs between managed services and self-hosted (e.g., MongoDB Atlas vs EC2 replica set)

---

## VisionSync Architecture Summary

```
Internet → CloudFront CDN → ALB (public subnets: 1a, 1b)
                                 ↓
                   Backend EC2 ASG (private subnets: 1a, 1b)
                        ↓           ↓           ↓
                   MongoDB RS    Redis EC2    SQS Queue
                   (3 nodes,     (1 node,        ↓
                    zone 1c)     zone 1c)    Lambda Fn
                                                  ↓
                                          ECS Fargate (Spot/On-Demand)
                                                  ↓
                                          S3 Processed Bucket
                                                  ↓
                                          CloudFront CDN
```

---

## Practices

### 1. Apply the Well-Architected Framework to Every Design Decision

**Five Pillars applied to VisionSync:**

| Pillar | VisionSync Implementation | Gap / Improvement |
|--------|---------------------------|-------------------|
| **Operational Excellence** | Makefile automation, Ansible IaC, CloudWatch logs | Add runbooks for common failures |
| **Security** | Private subnets, IAM least privilege, Bastion SSH | Consider AWS Systems Manager Session Manager to eliminate Bastion |
| **Reliability** | MongoDB replica set, ALB health checks, Multi-AZ | No cross-region DR; single Redis is SPOF |
| **Performance Efficiency** | ECS Fargate Spot, CloudFront CDN, DASH ABR | Consider ElastiCache for Redis (managed) |
| **Cost Optimization** | 70% Spot for ECS, S3 lifecycle, CloudFront caching | Reserved Instances for always-on EC2 (backend, bastion) |

**When designing a new component**, walk through each pillar and document decisions.

---

### 2. Select the Right Compute Service

**Decision framework for VisionSync workloads:**

| Use Case | Recommended Service | Reason |
|---|---|---|
| Long-running video transcoding (minutes) | **ECS Fargate** | No timeout limit, can use Spot, 2+ vCPU/4GB+ RAM |
| Short event-driven orchestration (<15 min) | **Lambda** | Serverless, cost-effective for SQS trigger handling |
| Stateful API server with WebSockets | **EC2 in ASG** | Lambda cold starts hurt Socket.IO; EC2 maintains connections |
| Batch lightweight jobs | **Lambda + ECS Batch mode** | Cost-efficient for small video files |
| Static frontend | **S3 + CloudFront** | Zero server cost, global low-latency |

**Wrong** — putting Socket.IO on Lambda:
```
Lambda: stateless, 15-min timeout, cold starts break WebSocket handshakes
```

**Right** — Socket.IO on EC2 with Redis adapter for multi-instance:
```
EC2 ASG (1-5 instances) + Redis Socket.IO adapter for cross-instance event broadcast
```

---

### 3. Design IAM Roles with Least Privilege

**Why**: Overly permissive IAM roles are the #1 AWS security risk. Each service should have exactly the permissions it needs — nothing more.

**VisionSync role mapping:**

```
Backend EC2 Role:
  ✅ s3:PutObject, s3:GetObject     → raw bucket (upload presigned URL generation)
  ✅ s3:GetObject                   → processed bucket (read only)
  ✅ sqs:SendMessage                → video processing queue
  ✅ cloudfront:CreateInvalidation  → cache invalidation after new video
  ❌ iam:*, ec2:*, s3:DeleteBucket  → NOT needed

ECS Task Role:
  ✅ s3:GetObject                   → raw bucket (download source video)
  ✅ s3:PutObject                   → processed bucket (upload chunks)
  ✅ ecr:GetAuthorizationToken      → pull container image
  ❌ sqs:*, lambda:*                → NOT needed

Lambda Role:
  ✅ ecs:RunTask                    → launch Fargate task
  ✅ iam:PassRole                   → pass ECS task role
  ✅ sqs:ReceiveMessage, sqs:DeleteMessage → consume queue
  ❌ s3:*, ec2:*                    → NOT needed
```

**Pattern — use condition keys to scope S3 access to specific buckets:**
```json
{
  "Effect": "Allow",
  "Action": ["s3:PutObject", "s3:GetObject"],
  "Resource": "arn:aws:s3:::vision-sync-raw-bucket/*"
}
```

---

### 4. Design for Cost Optimization from Day One

**VisionSync cost levers:**

| Resource | Strategy | Savings |
|---|---|---|
| ECS Fargate (video processing) | 70% FARGATE_SPOT | ~70% on compute |
| Backend EC2 (always on) | 1-year Reserved Instance (t3.medium) | ~40% vs On-Demand |
| Bastion EC2 (rarely used) | Schedule stop/start with Lambda | ~95% if stopped 23hrs/day |
| S3 raw videos (temp) | Lifecycle: delete after 7 days | Eliminates ongoing raw storage |
| S3 processed videos (hot) | Lifecycle: move to S3-IA after 30 days | ~40% on storage |
| CloudFront | Cache-Control headers on `.mpd` and `.m4s` | Reduces S3 egress costs |
| MongoDB EC2 | Rightsize: t3.medium (DB rarely CPU-bound) | vs oversized t3.large |

**S3 Lifecycle policy pattern:**
```typescript
// In IaC/src/storage/s3.ts
rawBucketLifecycle: {
  rules: [{
    id: "delete-raw-after-processing",
    status: "Enabled",
    expiration: { days: 7 }  // Delete raw videos after 7 days
  }]
},
processedBucketLifecycle: {
  rules: [{
    id: "transition-to-ia",
    status: "Enabled",
    transitions: [
      { days: 30, storageClass: "STANDARD_IA" },
      { days: 90, storageClass: "GLACIER_IR" }
    ]
  }]
}
```

---

### 5. Multi-AZ Design Principles

**Current state of VisionSync:**

```
Component        AZ Coverage           HA Status
─────────────────────────────────────────────────
ALB              1a + 1b               ✅ Multi-AZ
Backend ASG      1a + 1b               ✅ Multi-AZ
MongoDB          1c only (3 nodes)     ⚠️  Single-AZ (same zone)
Redis            1c (1 node)           ❌ Single point of failure
ECS Fargate      Dynamic (any AZ)      ✅ Multi-AZ
CloudFront       Global                ✅ Global
```

**Recommendations for improving HA:**

1. **MongoDB**: Spread replica set across 1b and 1c (primary in 1b, secondaries in 1b + 1c) — protects against zone 1c outage
2. **Redis**: Use ElastiCache with Multi-AZ replica, or at minimum add a standby Redis with Sentinel
3. **NAT Gateway**: Deploy one per AZ (currently single NAT is a SPOF for private subnet internet egress)

---

### 6. Service Selection: When to Use SQS vs SNS vs EventBridge

**VisionSync currently uses**: SQS (video processing jobs)

| Service | Use When | VisionSync Example |
|---|---|---|
| **SQS** | One consumer, decoupled async processing, retry needed | ✅ Video processing queue (Lambda consumes, retries on failure) |
| **SNS** | Fan-out to multiple consumers, pub/sub | Use if you add email notifications + analytics + processing simultaneously |
| **EventBridge** | Complex routing rules, scheduled events, cross-account | Use if you add scheduled cleanup jobs or multi-region event routing |

**Pattern to add email notification without changing SQS:**
```
S3 Upload → SQS → Lambda (ECS launcher)
                ↓ SNS topic (fan-out)
           → Email notification (SES)
           → Analytics pipeline (Kinesis)
```

---

### 7. Security Group Design — Minimal Egress/Ingress

**Detection signals**:
- Security groups with `0.0.0.0/0` on inbound ports other than 80/443
- Outbound `0.0.0.0/0` on all ports (overly permissive)
- Private instances with direct internet access

**Right — VisionSync security group model:**

```
Bastion SG:
  Ingress: 22 from YOUR_IP/32 only (not 0.0.0.0/0)
  Egress:  22 to Private Subnet CIDR (10.10.3.0/24, 10.10.4.0/24, 10.10.5.0/24)

ALB SG:
  Ingress: 80, 443 from 0.0.0.0/0
  Egress:  5000 to Backend SG

Backend SG:
  Ingress: 5000 from ALB SG only
  Ingress: 22 from Bastion SG only
  Egress:  27017 to MongoDB SG, 6379 to Redis SG, 443 to 0.0.0.0/0 (AWS APIs)

MongoDB SG:
  Ingress: 27017 from Backend SG only
  Ingress: 27017 from MongoDB SG (inter-replica communication)
  Ingress: 22 from Bastion SG only
```

---

## Quick Reference — Service Decisions

| Need | Choose | Avoid |
|---|---|---|
| Managed MongoDB | DocumentDB or Atlas | Self-managed EC2 (ops burden) |
| Managed Redis | ElastiCache | Self-managed EC2 Redis |
| HTTP video delivery | CloudFront + S3 | EC2-served static files |
| Long video processing | ECS Fargate | Lambda (15-min limit) |
| Real-time WebSockets | EC2 + Socket.IO | Lambda (stateless) |
| Event queue with retry | SQS + DLQ | Direct HTTP calls between services |
| SSH to private instances | SSM Session Manager | Bastion host (eliminates SSH key mgmt) |
| Secrets management | AWS Secrets Manager or SSM Parameter Store | `.env` files with plaintext secrets in Git |

## AWS Well-Architected Review Checklist

- [ ] Every IAM role uses least-privilege (no `*` actions/resources)
- [ ] No hardcoded credentials in code or `.env` committed to Git
- [ ] All sensitive data in private subnets
- [ ] Multi-AZ for stateful services (DB, cache)
- [ ] DLQ on every SQS queue
- [ ] CloudWatch alarms on all critical metrics (CPU, queue depth, error rate)
- [ ] S3 lifecycle policies on all buckets
- [ ] Spot interrupt handling in all ECS tasks
- [ ] ALB access logs enabled
- [ ] VPC Flow Logs enabled for security auditing
