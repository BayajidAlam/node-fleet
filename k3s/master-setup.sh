#!/bin/bash
set -e

echo "🚀 Setting up K3s Master Node..."

# Update system
echo "📦 Updating system packages..."
sudo apt-get update -qq
sudo apt-get install -y curl wget jq

# Install K3s server (master)
echo "🎯 Installing K3s server..."
curl -sfL https://get.k3s.io | sh -s - server \
  --disable traefik \
  --disable servicelb \
  --write-kubeconfig-mode 644

# Wait for K3s to be ready
echo "⏳ Waiting for K3s to be ready..."
until sudo k3s kubectl get nodes | grep -q "Ready"; do
  echo "   Still waiting..."
  sleep 5
done

echo "✅ K3s master is ready!"

# Get K3s token and update Secrets Manager
echo "🔐 Updating K3s token in Secrets Manager..."
K3S_TOKEN=$(sudo cat /var/lib/rancher/k3s/server/node-token)
aws secretsmanager update-secret \
  --secret-id node-fleet/k3s-token \
  --secret-string "$K3S_TOKEN" \
  --region ap-south-1

# Install Prometheus
echo "📊 Installing Prometheus..."
cat <<EOF | sudo k3s kubectl apply -f -
apiVersion: v1
kind: Namespace
metadata:
  name: monitoring
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-config
  namespace: monitoring
data:
  prometheus.yml: |
    global:
      scrape_interval: 15s
      evaluation_interval: 15s
    
    scrape_configs:
      - job_name: 'kubernetes-nodes'
        kubernetes_sd_configs:
          - role: node
        relabel_configs:
          - source_labels: [__address__]
            regex: '(.*):10250'
            replacement: '\${1}:9100'
            target_label: __address__
      
      - job_name: 'kubernetes-pods'
        kubernetes_sd_configs:
          - role: pod
        relabel_configs:
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
            action: keep
            regex: true
          - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
            action: replace
            target_label: __metrics_path__
            regex: (.+)
          - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
            action: replace
            regex: ([^:]+)(?::\d+)?;(\d+)
            replacement: \$1:\$2
            target_label: __address__
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus
  namespace: monitoring
spec:
  replicas: 1
  selector:
    matchLabels:
      app: prometheus
  template:
    metadata:
      labels:
        app: prometheus
    spec:
      containers:
      - name: prometheus
        image: prom/prometheus:latest
        args:
          - '--config.file=/etc/prometheus/prometheus.yml'
          - '--storage.tsdb.path=/prometheus'
          - '--storage.tsdb.retention.time=7d'
        ports:
        - containerPort: 9090
        volumeMounts:
        - name: prometheus-config
          mountPath: /etc/prometheus
        - name: prometheus-storage
          mountPath: /prometheus
      volumes:
      - name: prometheus-config
        configMap:
          name: prometheus-config
      - name: prometheus-storage
        emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: prometheus
  namespace: monitoring
spec:
  type: NodePort
  selector:
    app: prometheus
  ports:
    - port: 9090
      targetPort: 9090
      nodePort: 30090
EOF

# Install Grafana
echo "📈 Installing Grafana..."
cat <<EOF | sudo k3s kubectl apply -f -
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
        - name: GF_INSTALL_PLUGINS
          value: "grafana-piechart-panel"
        volumeMounts:
        - name: grafana-storage
          mountPath: /var/lib/grafana
      volumes:
      - name: grafana-storage
        emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: grafana
  namespace: monitoring
spec:
  type: NodePort
  selector:
    app: grafana
  ports:
    - port: 3000
      targetPort: 3000
      nodePort: 3000
EOF

# Wait for Prometheus and Grafana to be ready
echo "⏳ Waiting for monitoring stack..."
sleep 30

echo ""
echo "✅ K3s Master Setup Complete!"
echo "========================================="
echo "Node status:"
sudo k3s kubectl get nodes
echo ""
echo "Monitoring pods:"
sudo k3s kubectl get pods -n monitoring
echo ""
echo "Access points:"
echo "  Prometheus: http://\$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):30090"
echo "  Grafana:    http://\$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):3000"
echo "  Username: admin, Password: admin123"
echo ""
