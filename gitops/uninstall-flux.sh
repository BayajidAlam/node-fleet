#!/bin/bash
# Uninstall FluxCD from the cluster

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}=== Uninstalling FluxCD ===${NC}"

# Check if flux is installed
if ! command -v flux &> /dev/null; then
    echo -e "${RED}Flux CLI not found. Please install it first.${NC}"
    exit 1
fi

# Confirm uninstallation
read -p "Are you sure you want to uninstall FluxCD? (yes/no): " -r
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    echo "Uninstallation cancelled."
    exit 0
fi

# Suspend all Flux resources
echo "Suspending Flux resources..."
flux suspend kustomization --all

# Uninstall Flux
echo "Uninstalling Flux..."
flux uninstall --silent

echo -e "${GREEN}✓ FluxCD uninstalled successfully${NC}"
echo "Note: GitOps manifests in the repository are preserved."
