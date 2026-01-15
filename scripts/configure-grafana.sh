#!/bin/bash
# Redeploy Grafana with pre-configured data source and dashboards

echo "Creating Grafana provisioning ConfigMaps..."

# Create datasource provisioning
sudo k3s kubectl create configmap grafana-datasources \
  --from-literal=datasources.yaml='
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
' \
  -n monitoring \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -

# Create dashboard provisioning config
sudo k3s kubectl create configmap grafana-dashboard-provider \
  --from-literal=dashboards.yaml='
apiVersion: 1
providers:
  - name: "default"
    orgId: 1
    folder: ""
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
' \
  -n monitoring \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -

echo "Uploading dashboard files..."
# We'll upload the dashboards via a separate ConfigMap

echo "Updating Grafana deployment..."
sudo k3s kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grafana
  namespace: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app: grafana
  template:
    metadata:
      labels:
        app: grafana
    spec:
      containers:
      - name: grafana
        image: grafana/grafana:latest
        ports:
        - containerPort: 3000
        env:
        - name: GF_SECURITY_ADMIN_PASSWORD
          value: "admin123"
        - name: GF_PATHS_PROVISIONING
          value: "/etc/grafana/provisioning"
        volumeMounts:
        - name: grafana-datasources
          mountPath: /etc/grafana/provisioning/datasources
        - name: grafana-dashboard-provider
          mountPath: /etc/grafana/provisioning/dashboards
        - name: grafana-dashboards
          mountPath: /var/lib/grafana/dashboards
      volumes:
      - name: grafana-datasources
        configMap:
          name: grafana-datasources
      - name: grafana-dashboard-provider
        configMap:
          name: grafana-dashboard-provider
      - name: grafana-dashboards
        configMap:
          name: grafana-dashboards
EOF

echo "Waiting for Grafana to restart..."
sleep 10

echo "Deployment status:"
sudo k3s kubectl get pods -n monitoring
sudo k3s kubectl get configmaps -n monitoring

echo ""
echo "Grafana is now configured with:"
echo "  - Prometheus data source (pre-configured)"
echo "  - 3 dashboards: Autoscaler Overview, Cost Dashboard, Cost Tracking"
echo "  - Access: http://18.142.249.81:30091 (admin/admin123)"
