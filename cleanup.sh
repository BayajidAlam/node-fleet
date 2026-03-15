#!/bin/bash
# Cleanup script - removes unnecessary, temporary, and security-risk files

set -e
cd "$(dirname "$0")"

echo "Removing security-risk files..."
rm -f full_kubeconfig.yaml
rm -f temp_kubeconfig.yaml
rm -f node-fleet-key.pem
rm -f git_commands.bat

echo "Removing pulumi temp/generated files..."
rm -f pulumi/key.pem
rm -f pulumi/k3s.yaml
rm -f pulumi/k3s_new.yaml
rm -f pulumi/pulumi_error.log
rm -f pulumi/pulumi_full.log
rm -f pulumi/check_metrics.py
rm -f pulumi/check_targets.py
rm -f pulumi/check_targets_detailed.py
rm -f pulumi/generate_datasources.py
rm -f pulumi/generate_datasources_secure.py
rm -f pulumi/generate_datasources_url_auth.py
rm -f pulumi/generate_manifest.py
rm -f pulumi/dashboards-only.b64
rm -f pulumi/dashboards-only.yaml
rm -f pulumi/exporters.b64
rm -f pulumi/exporters.yaml
rm -f pulumi/grafana-dashboards.yaml
rm -f pulumi/grafana-datasources.yaml
rm -f pulumi/grafana-patch.b64
rm -f pulumi/grafana-patch.yaml
rm -f pulumi/grafana-restore.b64
rm -f pulumi/grafana-restore.yaml
rm -f pulumi/prometheus-config-update.b64
rm -f pulumi/prometheus-config-update.yaml
rm -f pulumi/ksm-rbac.yaml
rm -f pulumi/prometheus-rbac.yaml
rm -f pulumi/ec2-worker.ts
rm -f pulumi/index.ts
rm -rf pulumi/dist

echo "Removing test output files..."
rm -f tests/lambda/test_output.txt
rm -f tests/test_results.txt

echo ""
echo "Done! Now run:"
echo "  git add -A && git commit -m 'chore: remove unnecessary temp and security-risk files' && git push"
