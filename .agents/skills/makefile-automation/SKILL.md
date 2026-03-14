---
name: makefile-automation
version: 1.0.0
description: Best practices for working with the VisionSync Makefile. Covers target organization, dependency ordering, ECR login, Pulumi output extraction, Ansible inventory generation, and common deployment patterns.
---

> 📋 **Always read [`.agents/CONTEXT.md`](./../CONTEXT.md) first** — it contains the full project architecture, env vars, API routes, and infrastructure details for VisionSync.

# Makefile Automation Skill

## When to Use This Skill

Invoke this skill when:
- Adding new `make` targets to `Makefile`
- Debugging a broken `make` command
- Understanding the correct order of deployment steps
- Extracting Pulumi output values for use in scripts
- Automating a new operational workflow

---

## Practices

### 1. Extract Pulumi Outputs with `pulumi stack output`

**Why**: Many Makefile targets depend on dynamic AWS values (IPs, bucket names, URLs). These must come from Pulumi stack outputs, not hardcoded values.

**Detection signals**:
- Makefile target uses hardcoded IPs or ARNs
- Target breaks after infrastructure is redeployed
- `make update-env` generates wrong values

**Right**:
```makefile
# Extract Pulumi outputs safely using shell
PULUMI_OUTPUTS := $(shell cd IaC && pulumi stack output --json --stack dev 2>/dev/null)
BASTION_IP     := $(shell cd IaC && pulumi stack output bastionPublicIp --stack dev 2>/dev/null)
BACKEND_IP     := $(shell cd IaC && pulumi stack output backendPrivateIp --stack dev 2>/dev/null)
ALB_DNS        := $(shell cd IaC && pulumi stack output loadBalancerDnsName --stack dev 2>/dev/null)
```

---

### 2. Gate Destructive Targets with Confirmation

**Why**: `make destroy` deletes all AWS infrastructure. `make docker-clean` removes all Docker images. These should never run accidentally.

**Detection signals**:
- Destructive targets run silently without prompting
- No confirmation step before `pulumi destroy`

**Right**:
```makefile
destroy: ## Destroy all AWS infrastructure (DESTRUCTIVE!)
	@echo "⚠️  WARNING: This will destroy ALL AWS infrastructure!"
	@echo "Type 'yes' to confirm:"
	@read confirm && [ "$$confirm" = "yes" ] || (echo "Aborted."; exit 1)
	cd IaC && pulumi destroy --stack dev

docker-clean: ## Remove all Docker images and containers
	@echo "🧹 Cleaning Docker resources..."
	docker system prune -af --volumes
```

---

### 3. Use `.PHONY` for All Non-File Targets

**Why**: If a file with the same name as a target exists (e.g., a file named `build`), Make won't run the target. Declaring targets as `.PHONY` prevents this.

**Detection signals**:
- `make build` says `'build' is up to date` and does nothing
- Target works on CI but not locally

**Right**:
```makefile
.PHONY: deploy deploy-all deploy-fast install build clean \
        push-containers setup-mongodb setup-redis status \
        outputs update-env logs-backend logs-ecs logs-lambda
```

---

### 4. Always Login to ECR Before Docker Push/Pull

**Why**: ECR auth tokens expire every 12 hours. Any target that does `docker push` or `docker pull` from ECR must include an ECR login step.

**Detection signals**:
- `docker: denied: Your authorization token has expired`
- Container push succeeds in the morning but fails in the afternoon
- Ansible deploy fails because `docker pull` from ECR is denied

**Right**:
```makefile
ECR_URL  := $(shell cd IaC && pulumi stack output ecrRepositoryUrl --stack dev 2>/dev/null)
AWS_REGION := ap-southeast-1

ecr-login: ## Authenticate Docker with ECR
	aws ecr get-login-password --region $(AWS_REGION) | \
	docker login --username AWS --password-stdin $(ECR_URL)

push-backend: ecr-login ## Build and push backend image to ECR
	docker build -t vision-sync-backend ./server
	docker tag vision-sync-backend:latest $(ECR_URL)/vision-sync-backend:latest
	docker push $(ECR_URL)/vision-sync-backend:latest

push-containers: ecr-login push-backend push-frontend push-container
	@echo "✅ All containers pushed to ECR"
```

---

### 5. Add a `help` Target Showing All Commands

**Why**: The Makefile is 35KB and growing. New team members or the AI agent needs to quickly understand available commands. A `help` target that parses `##` comments is the standard pattern.

**Right**:
```makefile
help: ## Show all available commands
	@echo ""
	@echo "VisionSync Deployment Commands:"
	@echo "================================"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2}'
	@echo ""

.DEFAULT_GOAL := help
```

---

### 6. Create Ansible Inventory from Pulumi Outputs in One Target

**Why**: After every `make deploy`, Ansible inventory needs to be updated with new IPs. This should be a single, reliable target that fetches all IPs and writes the inventory.

**Right**:
```makefile
create-inventory: ## Generate Ansible inventory from Pulumi outputs
	@echo "📋 Generating Ansible inventory..."
	$(eval BASTION := $(shell cd IaC && pulumi stack output bastionPublicIp --stack dev))
	$(eval BACKEND := $(shell cd IaC && pulumi stack output backendPrivateIp --stack dev))
	$(eval MONGO1  := $(shell cd IaC && pulumi stack output mongodbNodes --json --stack dev | jq -r '.primary'))
	$(eval REDIS   := $(shell cd IaC && pulumi stack output redisEndpoint --stack dev))
	@envsubst < ansible/inventory.template > ansible/hosts.ini
	@echo "✅ Inventory written to ansible/hosts.ini"
```

---

## Quick Reference

| Pattern | Purpose |
|---------|---------|
| `$(shell cd IaC && pulumi stack output X)` | Get dynamic Pulumi value |
| `.PHONY: target` | Prevent file-name collision |
| `target: dep1 dep2` | Express dependencies between targets |
| `@read confirm && [ ... ]` | Prompt before destructive action |
| `## Comment` on target line | Shows in `make help` output |
| `ecr-login` as dependency | Ensure auth before any ECR push/pull |

## VisionSync Deployment Order

```
make install          # 1. Install all deps
make deploy           # 2. Provision AWS infra (Pulumi)
make create-inventory # 3. Generate Ansible inventory
make update-env       # 4. Populate server/.env from Pulumi
make setup-all-db     # 5. Init MongoDB replica + Redis
make push-containers  # 6. Build + push all Docker images to ECR
make deploy-services  # 7. Run Ansible to deploy containers
make status           # 8. Verify everything is healthy
```
