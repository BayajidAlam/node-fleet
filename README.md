# NodeFleet K3s Autoscaler

Intelligent, cost-optimized Kubernetes autoscaler for AWS with Spot instances, Multi-AZ support, and predictive scaling.

## Features

✅ **Core Autoscaling**
- Prometheus-based metrics (CPU, memory, pending pods)
- Intelligent scaling decisions (70% up, 30% down thresholds)
- Automated EC2 provisioning with K3s auto-join
- Graceful node deprovisioning (kubectl drain)
- DynamoDB state management with race condition prevention

✅ **Bonus Features** (ALL INCLUDED)
- 🌍 Multi-AZ distribution for resilience
- 💰 Spot instances (70% mix) - 60-70% cost reduction
- ⚡ Spot interruption handling (2-min warning auto-drain)
- 🔮 Predictive scaling (historical pattern analysis)
- 🎯 Custom app metrics integration
- 🔄 GitOps with FluxCD
- 📊 Real-time cost dashboard

## Cost Savings

| Setup | Monthly Cost | Savings |
|-------|--------------|---------|
| Without autoscaler | $180 | 0% |
| **With NodeFleet** | **$70-83** | **54-58%** 🎯 |

## Quick Start

```bash
# 1. Initialize project (one-time setup)
chmod +x scripts/init-project.sh
./scripts/init-project.sh

# 2. Deploy everything (fully automated)
chmod +x scripts/deploy-node-fleet.sh
./scripts/deploy-node-fleet.sh
```

## Documentation

- [Complete Implementation Plan](docs/COMPLETE_IMPLEMENTATION_PLAN.md)
- [Bonus Features Guide](docs/BONUS_FEATURES_GUIDE.md)
- [Technology Stack](docs/TECHNOLOGY_STACK.md)

## Requirements

- Node.js 18+
- Python 3.11
- AWS CLI configured
- Pulumi CLI
- kubectl

## License

MIT
