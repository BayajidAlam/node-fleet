# GitOps Integration with FluxCD

## Overview

This implementation integrates FluxCD for continuous delivery and GitOps workflows in the node-fleet K3s autoscaler project. FluxCD automatically syncs cluster state with Git repository, enabling declarative infrastructure management and automated deployments.

## Architecture

### GitOps Workflow

```
GitHub Repository → FluxCD → Kubernetes Cluster
     (Source)      (Sync)     (Apply)
```

**Key Components:**

- **GitRepository**: Monitors GitHub for changes
- **Kustomization**: Applies manifests from repository paths
- **ImageUpdateAutomation**: Auto-updates container images
- **AlertManager**: Sends notifications for reconciliation events

### Directory Structure

```
gitops/
├── install-flux.sh              # FluxCD installation script
├── uninstall-flux.sh            # Cleanup script
├── check-status.sh              # Status verification
├── reconcile.sh                 # Force sync
├── clusters/
│   └── production/
│       ├── kustomization.yaml   # Root kustomization
│       ├── infrastructure.yaml  # Infrastructure sync
│       ├── apps.yaml            # Application sync
│       ├── monitoring.yaml      # Monitoring sync
│       └── image-automation.yaml # Auto-update config
├── apps/
│   └── demo-app/
│       ├── kustomization.yaml
│       ├── deployment.yaml      # App deployment
│       ├── service.yaml         # NodePort service
│       └── autoscaler.yaml      # HPA configuration
├── infrastructure/
│   ├── kustomization.yaml
│   ├── prometheus.yaml          # Prometheus setup
│   └── grafana.yaml             # Grafana setup
└── monitoring/
    ├── kustomization.yaml
    └── alerts.yaml              # Prometheus alert rules
```

## Installation

### Prerequisites

1. K3s cluster running
2. kubectl configured
3. GitHub personal access token with repo permissions

### Setup Steps

1. **Set Environment Variables**

```bash
export GITHUB_TOKEN=<your-github-token>
export GITHUB_USER=<your-github-username>
export GITHUB_REPO=node-fleet
```

2. **Run Installation Script**

```bash
cd gitops
./install-flux.sh
```

The script will:

- Install Flux CLI
- Run pre-flight checks
- Bootstrap FluxCD in cluster
- Create GitOps directory structure
- Configure source controllers

3. **Verify Installation**

```bash
./check-status.sh
```

## How It Works

### 1. Continuous Reconciliation

FluxCD polls the Git repository every **1 minute** (configurable) and applies changes:

- **Infrastructure** (10m interval): Prometheus, Grafana
- **Apps** (5m interval): demo-app deployment
- **Monitoring** (10m interval): Alert rules

### 2. Image Automation

When new container images are pushed:

1. FluxCD detects new semver tags
2. Updates deployment manifest automatically
3. Commits change to Git repository
4. Applies updated manifest to cluster

### 3. Dependency Management

Resources are applied in order:

1. Infrastructure (Prometheus, Grafana)
2. Apps (depends on infrastructure)
3. Monitoring (depends on infrastructure)

### 4. Drift Detection

If manual changes are made to cluster:

- FluxCD detects drift
- Reverts to Git state within reconciliation interval
- Ensures cluster matches repository

## Usage

### Force Reconciliation

```bash
./reconcile.sh
```

### Check Status

```bash
./check-status.sh
```

### View Logs

```bash
flux logs --follow
```

### Suspend Reconciliation

```bash
flux suspend kustomization apps
```

### Resume Reconciliation

```bash
flux resume kustomization apps
```

## Integration with Autoscaler

### Lambda Integration

The autoscaler Lambda function interacts with FluxCD indirectly:

1. **Scaling Events** trigger node changes
2. **Prometheus** scrapes metrics from all nodes
3. **FluxCD** ensures monitoring stack is always running
4. **Alerts** configured via GitOps fire when thresholds breach

### Deployment Flow

```
Developer → Git Push → FluxCD Detects → Apply to K3s → Prometheus Scrapes → Lambda Scales
```

## Security

### RBAC Configuration

FluxCD runs with minimal permissions:

- **ServiceAccount**: `flux-system/source-controller`
- **ClusterRole**: Limited to Flux resources
- **Secrets**: GitHub token stored in `flux-system` namespace

### Secret Management

```bash
# Create GitHub credentials
flux create secret git flux-system \
  --url=https://github.com/${GITHUB_USER}/${GITHUB_REPO} \
  --username=${GITHUB_USER} \
  --password=${GITHUB_TOKEN}
```

## Monitoring

### FluxCD Metrics

Exposed metrics (Prometheus format):

- `gotk_reconcile_duration_seconds` - Reconciliation latency
- `gotk_reconcile_condition` - Success/failure status
- `gotk_suspend_status` - Suspended resources

### Alerts

Configured in [alerts.yaml](monitoring/alerts.yaml):

- **ReconciliationFailure**: FluxCD fails to sync
- **SourceNotReady**: Git repository unreachable
- **KustomizationNotReady**: Manifest application failed

## Troubleshooting

### Reconciliation Fails

```bash
# Check events
flux events --for Kustomization/apps

# Check logs
flux logs --kind=Kustomization --name=apps

# Force reconciliation
flux reconcile kustomization apps --with-source
```

### Image Automation Not Working

```bash
# Check image policy
flux get image policy demo-app

# Check image repository
flux get image repository demo-app

# Check automation logs
kubectl logs -n flux-system deploy/image-automation-controller
```

### Git Authentication Issues

```bash
# Recreate secret
kubectl delete secret flux-system -n flux-system
flux create secret git flux-system \
  --url=https://github.com/${GITHUB_USER}/${GITHUB_REPO} \
  --username=${GITHUB_USER} \
  --password=${GITHUB_TOKEN}
```

## Best Practices

### 1. Git Workflow

- **Main branch**: Production deployments
- **Feature branches**: Test changes before merge
- **Pull requests**: Review infrastructure changes
- **Semantic versioning**: Use semver for container images

### 2. Resource Organization

- Separate concerns: apps, infrastructure, monitoring
- Use namespaces for isolation
- Apply least privilege RBAC
- Document all manifests

### 3. Testing

Before pushing to main:

1. Test manifests locally with `kubectl apply --dry-run=client`
2. Validate kustomizations: `kustomize build gitops/apps/demo-app`
3. Check for breaking changes
4. Monitor FluxCD logs after push

### 4. Rollback

If deployment fails:

```bash
# Suspend auto-sync
flux suspend kustomization apps

# Revert Git commit
git revert <commit-hash>
git push

# Resume sync
flux resume kustomization apps
```

## Cost Optimization

FluxCD runs with minimal resources:

- **CPU**: 100m per controller
- **Memory**: 64Mi per controller
- **Replicas**: 1 (single master node)

Estimated overhead: **~300m CPU, ~256Mi memory**

## Comparison with Manual Deployment

| Aspect             | Manual kubectl | GitOps with FluxCD |
| ------------------ | -------------- | ------------------ |
| Deployment speed   | Immediate      | 1-5 minutes        |
| Audit trail        | None           | Git history        |
| Rollback           | Manual         | Git revert         |
| Drift detection    | Manual check   | Automatic          |
| Multi-cluster      | Difficult      | Declarative        |
| Secrets management | Manual         | Sealed Secrets     |

## Next Steps

1. **Add Slack Notifications**

   - Configure FluxCD Alert Provider
   - Send notifications to autoscaler Slack channel

2. **Implement Canary Deployments**

   - Use Flagger for progressive delivery
   - Auto-rollback on metric regression

3. **Multi-Cluster Support**

   - Define staging cluster configuration
   - Sync multiple clusters from single repository

4. **Sealed Secrets**
   - Encrypt secrets in Git
   - Use Bitnami Sealed Secrets controller

## References

- [FluxCD Documentation](https://fluxcd.io/docs/)
- [Kustomize Reference](https://kustomize.io/)
- [GitOps Principles](https://www.gitops.tech/)
- [K3s + Flux Guide](https://docs.k3s.io/advanced#gitops-with-flux)
