# GitOps Integration - Usage Guide

## Quick Start

### 1. Install FluxCD

```bash
export GITHUB_TOKEN=<your-token>
export GITHUB_USER=<your-username>
cd gitops
./install-flux.sh
```

### 2. Verify Installation

```bash
./check-status.sh
```

### 3. Monitor Deployments

```bash
# Watch all Flux resources
flux get all --watch

# Follow logs
flux logs --follow

# Check specific kustomization
flux get kustomizations
```

## Common Operations

### Force Sync

```bash
./reconcile.sh
```

### Suspend Auto-Sync

```bash
flux suspend kustomization apps
```

### Resume Auto-Sync

```bash
flux resume kustomization apps
```

### View Events

```bash
flux events --for Kustomization/apps
```

## Troubleshooting

### Check Flux System Health

```bash
flux check
```

### Debug Reconciliation Issues

```bash
# Check kustomization status
kubectl get kustomizations -n flux-system

# View detailed status
kubectl describe kustomization apps -n flux-system

# Check logs
kubectl logs -n flux-system deploy/kustomize-controller
```

### Authentication Problems

```bash
# Recreate Git credentials
kubectl delete secret flux-system -n flux-system
flux create secret git flux-system \
  --url=https://github.com/${GITHUB_USER}/${GITHUB_REPO} \
  --username=${GITHUB_USER} \
  --password=${GITHUB_TOKEN}
```

## GitOps Workflow

### Making Changes

1. **Edit manifests** in `gitops/` directory
2. **Commit and push** to Git
3. **FluxCD auto-syncs** within 1-5 minutes
4. **Monitor** with `./check-status.sh`

### Rollback

```bash
# Suspend auto-sync
flux suspend kustomization apps

# Revert Git commit
git revert <commit-hash>
git push

# Resume sync
flux resume kustomization apps
```

## Integration with Autoscaler

FluxCD manages the infrastructure that the Lambda autoscaler monitors:

- **Prometheus**: Metrics collection
- **Grafana**: Visualization
- **Demo App**: Workload for testing
- **Alerts**: Prometheus alert rules

The autoscaler Lambda scales nodes based on metrics from FluxCD-managed infrastructure.

## Next Steps

After GitOps is working:

1. Deploy autoscaler Lambda
2. Run load tests with k6
3. Monitor autoscaling behavior
4. Check FluxCD maintains monitoring stack during scale events
