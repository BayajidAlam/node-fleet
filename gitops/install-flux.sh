#!/bin/bash
# FluxCD installation and configuration script for node-fleet autoscaler
# This script sets up GitOps workflow using FluxCD

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
FLUX_VERSION="${FLUX_VERSION:-v2.2.0}"
GITHUB_USER="${GITHUB_USER:-}"
GITHUB_REPO="${GITHUB_REPO:-node-fleet}"
GITHUB_BRANCH="${GITHUB_BRANCH:-main}"
FLUX_NAMESPACE="flux-system"

echo -e "${GREEN}=== FluxCD Installation for node-fleet ===${NC}"

# Check prerequisites
check_prerequisites() {
    echo "Checking prerequisites..."
    
    # Check kubectl
    if ! command -v kubectl &> /dev/null; then
        echo -e "${RED}Error: kubectl is not installed${NC}"
        exit 1
    fi
    
    # Check cluster access
    if ! kubectl cluster-info &> /dev/null; then
        echo -e "${RED}Error: Cannot access Kubernetes cluster${NC}"
        exit 1
    fi
    
    # Check GitHub token
    if [ -z "$GITHUB_TOKEN" ]; then
        echo -e "${RED}Error: GITHUB_TOKEN environment variable not set${NC}"
        echo "Please set it with: export GITHUB_TOKEN=<your-token>"
        exit 1
    fi
    
    if [ -z "$GITHUB_USER" ]; then
        echo -e "${RED}Error: GITHUB_USER environment variable not set${NC}"
        echo "Please set it with: export GITHUB_USER=<your-github-username>"
        exit 1
    fi
    
    echo -e "${GREEN}✓ Prerequisites check passed${NC}"
}

# Install Flux CLI
install_flux_cli() {
    echo "Installing Flux CLI..."
    
    if command -v flux &> /dev/null; then
        echo "Flux CLI already installed: $(flux --version)"
        return
    fi
    
    curl -s https://fluxcd.io/install.sh | sudo bash
    
    echo -e "${GREEN}✓ Flux CLI installed${NC}"
}

# Pre-flight checks
flux_preflight_check() {
    echo "Running Flux pre-flight checks..."
    
    flux check --pre
    
    echo -e "${GREEN}✓ Pre-flight checks passed${NC}"
}

# Bootstrap FluxCD
bootstrap_flux() {
    echo "Bootstrapping FluxCD..."
    
    flux bootstrap github \
        --owner="${GITHUB_USER}" \
        --repository="${GITHUB_REPO}" \
        --branch="${GITHUB_BRANCH}" \
        --path="./gitops/clusters/production" \
        --personal \
        --private=false
    
    echo -e "${GREEN}✓ FluxCD bootstrapped successfully${NC}"
}

# Create GitOps directory structure
create_gitops_structure() {
    echo "Creating GitOps directory structure..."
    
    mkdir -p gitops/clusters/production/{apps,infrastructure,monitoring}
    mkdir -p gitops/apps/{demo-app,autoscaler}
    mkdir -p gitops/infrastructure/{k3s,prometheus,grafana}
    mkdir -p gitops/monitoring/{alerts,dashboards}
    
    echo -e "${GREEN}✓ GitOps structure created${NC}"
}

# Create Flux Kustomization for apps
create_app_kustomization() {
    cat > gitops/clusters/production/apps/kustomization.yaml <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../apps/demo-app
  - ../../apps/autoscaler
EOF
    
    echo -e "${GREEN}✓ App kustomization created${NC}"
}

# Create source controller for autoscaler configs
create_source_controller() {
    cat > gitops/clusters/production/source.yaml <<EOF
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: node-fleet
  namespace: ${FLUX_NAMESPACE}
spec:
  interval: 1m
  url: https://github.com/${GITHUB_USER}/${GITHUB_REPO}
  ref:
    branch: ${GITHUB_BRANCH}
EOF
    
    echo -e "${GREEN}✓ Source controller configured${NC}"
}

# Verify installation
verify_installation() {
    echo "Verifying FluxCD installation..."
    
    # Wait for Flux components
    kubectl wait --for=condition=ready pod \
        -l app.kubernetes.io/part-of=flux \
        -n ${FLUX_NAMESPACE} \
        --timeout=5m
    
    # Check Flux status
    flux check
    
    echo -e "${GREEN}✓ FluxCD is ready${NC}"
}

# Main execution
main() {
    check_prerequisites
    install_flux_cli
    flux_preflight_check
    bootstrap_flux
    create_gitops_structure
    create_app_kustomization
    create_source_controller
    verify_installation
    
    echo -e "${GREEN}"
    echo "=========================================="
    echo "FluxCD Installation Complete!"
    echo "=========================================="
    echo -e "${NC}"
    echo "Next steps:"
    echo "1. Commit and push the gitops/ directory"
    echo "2. FluxCD will automatically sync your cluster"
    echo "3. Monitor with: flux get all"
    echo "4. Check logs with: flux logs"
}

main "$@"
