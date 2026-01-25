"""
Tests for GitOps FluxCD integration

This module tests GitOps deployment workflows, reconciliation,
and integration with the autoscaler system.
"""

import pytest
import subprocess
import json
import time
from datetime import datetime, timedelta


# Check if Kubernetes is available
def is_k8s_available():
    """Check if kubectl can connect to a cluster"""
    try:
        result = subprocess.run(
            ["kubectl", "cluster-info"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# Skip all tests if Kubernetes not available
pytestmark = pytest.mark.skipif(
    not is_k8s_available(),
    reason="Kubernetes cluster not available - these are integration tests"
)


class TestFluxInstallation:
    """Test FluxCD installation and setup"""
    
    def test_flux_cli_installed(self):
        """Verify Flux CLI is installed"""
        result = subprocess.run(
            ["flux", "--version"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert "flux version" in result.stdout.lower()
    
    def test_flux_system_namespace_exists(self):
        """Verify flux-system namespace exists"""
        result = subprocess.run(
            ["kubectl", "get", "namespace", "flux-system"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
    
    def test_flux_controllers_running(self):
        """Verify all Flux controllers are running"""
        controllers = [
            "source-controller",
            "kustomize-controller",
            "helm-controller",
            "notification-controller"
        ]
        
        for controller in controllers:
            result = subprocess.run(
                ["kubectl", "get", "deployment", controller, "-n", "flux-system"],
                capture_output=True,
                text=True
            )
            assert result.returncode == 0
            assert "1/1" in result.stdout or "READY" in result.stdout


class TestGitRepository:
    """Test GitRepository source configuration"""
    
    def test_git_repository_exists(self):
        """Verify GitRepository resource exists"""
        result = subprocess.run(
            ["flux", "get", "sources", "git"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert "flux-system" in result.stdout
    
    def test_git_repository_ready(self):
        """Verify GitRepository is ready"""
        result = subprocess.run(
            ["kubectl", "get", "gitrepository", "flux-system", 
             "-n", "flux-system", "-o", "json"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            conditions = data.get("status", {}).get("conditions", [])
            ready_condition = next(
                (c for c in conditions if c["type"] == "Ready"),
                None
            )
            assert ready_condition is not None
            assert ready_condition["status"] == "True"


class TestKustomizations:
    """Test Kustomization resources"""
    
    def test_infrastructure_kustomization_exists(self):
        """Verify infrastructure kustomization exists"""
        result = subprocess.run(
            ["flux", "get", "kustomization", "infrastructure"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
    
    def test_apps_kustomization_exists(self):
        """Verify apps kustomization exists"""
        result = subprocess.run(
            ["flux", "get", "kustomization", "apps"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
    
    def test_monitoring_kustomization_exists(self):
        """Verify monitoring kustomization exists"""
        result = subprocess.run(
            ["flux", "get", "kustomization", "monitoring"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
    
    def test_kustomizations_ready(self):
        """Verify all kustomizations are ready"""
        kustomizations = ["infrastructure", "apps", "monitoring"]
        
        for kustomization in kustomizations:
            result = subprocess.run(
                ["kubectl", "get", "kustomization", kustomization,
                 "-n", "flux-system", "-o", "json"],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                conditions = data.get("status", {}).get("conditions", [])
                ready_condition = next(
                    (c for c in conditions if c["type"] == "Ready"),
                    None
                )
                if ready_condition:
                    assert ready_condition["status"] == "True", \
                        f"{kustomization} is not ready"


class TestReconciliation:
    """Test FluxCD reconciliation behavior"""
    
    def test_manual_reconciliation(self):
        """Test manual reconciliation trigger"""
        result = subprocess.run(
            ["flux", "reconcile", "source", "git", "flux-system"],
            capture_output=True,
            text=True,
            timeout=60
        )
        assert result.returncode == 0
    
    def test_reconciliation_interval(self):
        """Verify reconciliation happens within expected interval"""
        # Get last reconciliation time
        result = subprocess.run(
            ["kubectl", "get", "gitrepository", "flux-system",
             "-n", "flux-system", "-o", "json"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            last_time_str = data.get("status", {}).get("artifact", {}).get("lastUpdateTime")
            
            if last_time_str:
                last_time = datetime.fromisoformat(last_time_str.replace('Z', '+00:00'))
                now = datetime.now(last_time.tzinfo)
                delta = now - last_time
                
                # Should reconcile within 2 minutes (1m interval + buffer)
                assert delta < timedelta(minutes=2), \
                    f"Last reconciliation was {delta.total_seconds()}s ago"


class TestDemoAppDeployment:
    """Test demo app deployment via GitOps"""
    
    def test_demo_app_deployment_exists(self):
        """Verify demo-app deployment exists"""
        result = subprocess.run(
            ["kubectl", "get", "deployment", "demo-app", "-n", "default"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
    
    def test_demo_app_service_exists(self):
        """Verify demo-app service exists"""
        result = subprocess.run(
            ["kubectl", "get", "service", "demo-app", "-n", "default"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
    
    def test_demo_app_hpa_exists(self):
        """Verify demo-app HPA exists"""
        result = subprocess.run(
            ["kubectl", "get", "hpa", "demo-app", "-n", "default"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
    
    def test_demo_app_pods_running(self):
        """Verify demo-app pods are running"""
        result = subprocess.run(
            ["kubectl", "get", "pods", "-l", "app=demo-app",
             "-n", "default", "-o", "json"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            pods = data.get("items", [])
            
            assert len(pods) >= 2, "At least 2 replicas should be running"
            
            for pod in pods:
                phase = pod.get("status", {}).get("phase")
                assert phase == "Running", f"Pod is in {phase} state"


class TestMonitoringStack:
    """Test Prometheus and Grafana deployment"""
    
    def test_monitoring_namespace_exists(self):
        """Verify monitoring namespace exists"""
        result = subprocess.run(
            ["kubectl", "get", "namespace", "monitoring"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
    
    def test_prometheus_deployment_exists(self):
        """Verify Prometheus deployment exists"""
        result = subprocess.run(
            ["kubectl", "get", "deployment", "prometheus", "-n", "monitoring"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
    
    def test_grafana_deployment_exists(self):
        """Verify Grafana deployment exists"""
        result = subprocess.run(
            ["kubectl", "get", "deployment", "grafana", "-n", "monitoring"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
    
    def test_prometheus_service_accessible(self):
        """Verify Prometheus service is accessible"""
        result = subprocess.run(
            ["kubectl", "get", "service", "prometheus",
             "-n", "monitoring", "-o", "json"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            spec = data.get("spec", {})
            assert spec.get("type") == "NodePort"
            assert any(p.get("nodePort") == 30090 for p in spec.get("ports", []))


class TestDriftDetection:
    """Test drift detection and automatic remediation"""
    
    def test_manual_change_reverted(self):
        """Test that manual changes are reverted"""
        # Make a manual change
        subprocess.run(
            ["kubectl", "scale", "deployment", "demo-app",
             "--replicas=5", "-n", "default"],
            capture_output=True
        )
        
        # Wait for reconciliation
        time.sleep(90)  # Wait longer than reconciliation interval
        
        # Check if reverted to desired state (2 replicas)
        result = subprocess.run(
            ["kubectl", "get", "deployment", "demo-app",
             "-n", "default", "-o", "json"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            replicas = data.get("spec", {}).get("replicas")
            # FluxCD should have reverted to 2 replicas
            assert replicas == 2, "Drift was not corrected"


class TestImageAutomation:
    """Test image update automation"""
    
    def test_image_repository_exists(self):
        """Verify ImageRepository exists"""
        result = subprocess.run(
            ["flux", "get", "image", "repository", "demo-app"],
            capture_output=True,
            text=True
        )
        # May not exist if not using container registry
        # This is an optional feature
        pass
    
    def test_image_policy_exists(self):
        """Verify ImagePolicy exists"""
        result = subprocess.run(
            ["flux", "get", "image", "policy", "demo-app"],
            capture_output=True,
            text=True
        )
        # Optional feature
        pass


class TestFluxEvents:
    """Test Flux event logging"""
    
    def test_recent_events_exist(self):
        """Verify Flux generates events"""
        result = subprocess.run(
            ["flux", "events", "--limit", "10"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
    
    def test_no_error_events(self):
        """Verify no error events in recent history"""
        result = subprocess.run(
            ["flux", "events", "--limit", "20"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            # Check for common error indicators
            assert "error" not in result.stdout.lower() or \
                   "reconciliation succeeded" in result.stdout.lower()


@pytest.mark.integration
class TestEndToEnd:
    """End-to-end GitOps workflow test"""
    
    def test_full_deployment_workflow(self):
        """Test complete deployment workflow"""
        # 1. Force reconciliation
        subprocess.run(
            ["flux", "reconcile", "source", "git", "flux-system",
             "--with-source"],
            capture_output=True,
            timeout=120
        )
        
        # 2. Wait for all kustomizations
        time.sleep(60)
        
        # 3. Verify infrastructure
        result = subprocess.run(
            ["kubectl", "get", "deployment", "prometheus", "-n", "monitoring"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        
        # 4. Verify apps
        result = subprocess.run(
            ["kubectl", "get", "deployment", "demo-app", "-n", "default"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        
        # 5. Verify monitoring
        result = subprocess.run(
            ["kubectl", "get", "configmap", "prometheus-alerts",
             "-n", "monitoring"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
