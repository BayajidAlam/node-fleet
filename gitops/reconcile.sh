#!/bin/bash
# Force reconciliation of all Flux resources

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Forcing Flux Reconciliation ===${NC}"

# Reconcile Git repository
echo -e "\n${YELLOW}Reconciling Git repository...${NC}"
flux reconcile source git flux-system --with-source

# Reconcile infrastructure
echo -e "\n${YELLOW}Reconciling infrastructure...${NC}"
flux reconcile kustomization infrastructure

# Reconcile apps
echo -e "\n${YELLOW}Reconciling apps...${NC}"
flux reconcile kustomization apps

# Reconcile monitoring
echo -e "\n${YELLOW}Reconciling monitoring...${NC}"
flux reconcile kustomization monitoring

echo -e "\n${GREEN}✓ Reconciliation complete${NC}"
echo "Check status with: ./gitops/check-status.sh"
