#!/bin/bash
# Verify FluxCD deployment status

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== FluxCD Status Check ===${NC}"

# Check Flux system
echo -e "\n${YELLOW}1. Flux System Status:${NC}"
flux check

# Get all resources
echo -e "\n${YELLOW}2. All Flux Resources:${NC}"
flux get all

# Check Kustomizations
echo -e "\n${YELLOW}3. Kustomizations:${NC}"
flux get kustomizations

# Check GitRepositories
echo -e "\n${YELLOW}4. Git Repositories:${NC}"
flux get sources git

# Check recent events
echo -e "\n${YELLOW}5. Recent Events:${NC}"
flux events --for Kustomization/apps --limit 10

# Check reconciliation status
echo -e "\n${YELLOW}6. Reconciliation Status:${NC}"
flux reconcile source git flux-system --with-source

echo -e "\n${GREEN}✓ Status check complete${NC}"
