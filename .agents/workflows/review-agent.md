---
description: Review agent for VisionSync — cross-cutting code review, architecture proposals, and AWS diagrams
---

# VisionSync — Review Agent

> 📋 **Read [`.agents/CONTEXT.md`](./../CONTEXT.md) first** before any work. It contains the full architecture, API surface, infrastructure, and known gotchas.

## Scope
This agent handles cross-cutting quality work:
- Code review across **any** layer before merge or deployment
- Architecture proposals and tradeoff analysis
- AWS infrastructure diagrams
- Well-Architected Framework assessments

**Does NOT write production code** → use `/app-agent` or `/infra-agent` for that.

## Skills to Load
Before working, read the relevant skill:

- **[`code-reviewer`](./../skills/code-reviewer/SKILL.md)** — for any code review
- **[`aws-solution-architect`](./../skills/aws-solution-architect/SKILL.md)** — for architecture proposals
- **[`aws-diagrams`](./../skills/aws-diagrams/SKILL.md)** — for generating diagrams

## Code Review Checklist (Use Before Every Deploy)

### Critical (block deployment)
- [ ] No hardcoded credentials, IPs, or bucket names in code
- [ ] SIGTERM handler present in ECS container — Spot interruption handled
- [ ] SQS messages deleted **only after** ECS task launched successfully
- [ ] No resources created inside `.apply()` in Pulumi code
- [ ] S3 bucket has no public access — CloudFront only

### High (fix soon)
- [ ] MongoDB uses full replica set URI (`?replicaSet=rs0`)
- [ ] `app.set('trust proxy', 1)` present in Express setup
- [ ] DASH manifest validated before webhook sent
- [ ] ECR login before every `docker pull` in Ansible playbooks

### Medium (best practice)
- [ ] AWS SDK v3 pattern (`client.send(new XCommand(...))`)
- [ ] Socket.IO emits to specific room, not broadcast
- [ ] Rate limiting applied to upload endpoint (5 req/window)
- [ ] Mongoose schemas have `timestamps: true`

## Review Output Format
```
## Code Review: <file or change description>

### 🔴 Critical
- [SECURITY] ...

### 🟠 High
- [BUG] ...

### 🟡 Medium
- [PERF] ...

### 🟢 Low
- [STYLE] ...

### ✅ Looks Good
- ...
```

## Architecture Review
When evaluating a new component, check all 5 pillars:
1. **Operational Excellence** — how is it monitored and deployed?
2. **Security** — private subnet? least-privilege IAM? no public S3?
3. **Reliability** — what's the SPOF? what happens on failure?
4. **Performance Efficiency** — right service for the workload?
5. **Cost Optimization** — Spot where possible? lifecycle policies? right-sized?

## Known Architecture Weaknesses (for context)
- MongoDB: all 3 nodes in zone 1c — single-AZ, not HA
- Redis: single node — SPOF for rate limiting + Socket.IO backplane
- NAT Gateway: single instance — SPOF for private subnet internet egress
