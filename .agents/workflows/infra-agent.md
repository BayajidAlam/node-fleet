---
description: Infrastructure agent for VisionSync — manages IaC, Ansible deployments, Makefile automation, and AWS architecture decisions
---

# VisionSync — Infra Agent

> 📋 **Read [`.agents/CONTEXT.md`](./../CONTEXT.md) first** before any work. It contains the full architecture, env vars, infrastructure specs, and deployment order.

## Scope
This agent handles everything related to infrastructure and deployment:
- `IaC/` — Pulumi AWS infrastructure
- `ansible/` — Server configuration and deployment playbooks
- `Makefile` — Deployment automation
- AWS architecture decisions and cost optimization

## Skills to Load
Before working, read these skills in order:

1. **[`pulumi-best-practices`](./../skills/pulumi-best-practices/SKILL.md)** — before touching any `IaC/` code
2. **[`ansible-playbooks`](./../skills/ansible-playbooks/SKILL.md)** — before writing or running any playbook
3. **[`makefile-automation`](./../skills/makefile-automation/SKILL.md)** — before adding or debugging Makefile targets
4. **[`aws-solution-architect`](./../skills/aws-solution-architect/SKILL.md)** — before any architecture decision or service selection

## Deployment Order (Always Follow This)
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

## Key Rules for This Agent

### IaC (`IaC/`)
- Never create resources inside `.apply()` callbacks
- Always run `pulumi preview` before `pulumi up`
- Add `aliases` when renaming or moving resources to avoid destroy+recreate
- S3 Event Notification on the raw bucket must be configured to trigger SQS on `s3:ObjectCreated:*` for the `videos/` prefix
- SQS queue policy must allow `s3.amazonaws.com` as principal to call `sqs:SendMessage`

### Ansible (`ansible/`)
- Always SSH via bastion jump host for private instances
- Always ECR-login before any `docker pull`
- Tasks must be idempotent — use `when:` guards
- Regenerate inventory after every `make deploy`: `make create-inventory`

### Architecture Decisions
- Files < 1GB → 70% FARGATE_SPOT, 30% Regular (configurable via `ECS_SPOT_PERCENTAGE`)
- Files ≥ 1GB → Regular Fargate only
- If Spot unavailable → auto-retry on Regular
- MongoDB and Redis are EC2-based (not managed) — configured via Ansible post-provisioning
- Backend **no longer needs** `sqs:SendMessage` in its IAM role — S3 Event Notification handles SQS trigger directly

## Common Commands

```bash
# Infrastructure
make deploy              # Pulumi up
make destroy             # Destroy all (prompts for confirmation)
make outputs             # Show all Pulumi outputs
make status              # Overall health check

# Databases
make setup-mongodb       # Init MongoDB replica set
make setup-redis         # Setup Redis container
make check-mongodb       # Verify replica set health
make check-redis         # Verify Redis

# Deployment
make push-containers     # ECR login + build + push all images
make deploy-services     # Run Ansible to deploy to EC2
make deploy-fast         # Quick code update (rebuild + redeploy backend only)
make create-inventory    # Refresh Ansible inventory from Pulumi outputs

# Logs
make logs-backend        # Backend container logs
make logs-ecs            # ECS task logs
make logs-lambda         # Lambda function logs
```
