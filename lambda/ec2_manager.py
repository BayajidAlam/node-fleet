"""
EC2 Manager for K3s node lifecycle management.
Handles scale-up (EC2 launch) and scale-down (SSM async drain + termination).

Scale-down drain flow (keeps Lambda under 30s):
  - Initiates kubectl drain via SSM Run Command on master (non-blocking, <5s)
  - Stores drain state in DynamoDB
  - Next Lambda invocation checks SSM status and terminates if drain succeeded
"""

import os
import sys
import time
import logging
import boto3
from typing import Dict, List, Optional, Tuple
from multi_az_helper import select_subnet_for_new_instance

logger = logging.getLogger()

# SSM drain timeout: 300 seconds max per drain operation
DRAIN_TIMEOUT_SECONDS = 300
# How long to wait for a node to appear as Ready after launch (10 minutes)
NODE_READY_TIMEOUT_SECONDS = 600


def _get_boto3():
    """
    Return the boto3 module, always resolving through sys.modules['ec2_manager'].
    This ensures unittest.mock.patch('ec2_manager.boto3') is honoured even when
    multiple test files load this module and overwrite sys.modules['ec2_manager'].
    """
    mod = sys.modules.get("ec2_manager")
    if mod is not None and hasattr(mod, "boto3"):
        return mod.boto3
    return boto3


class EC2Manager:
    """Manages EC2 worker node lifecycle for K3s cluster."""

    def __init__(
        self,
        worker_template_id: str,
        worker_spot_template_id: str = None,
        spot_percentage: int = 70,
    ):
        self.worker_template_id = worker_template_id
        self.worker_spot_template_id = worker_spot_template_id
        self.spot_percentage = spot_percentage
        b3 = _get_boto3()
        self.ec2 = b3.client("ec2")
        self.ssm = b3.client("ssm")
        # Discover subnets at init time and cache; tests inspect this attribute
        self.available_subnets: List[str] = self._discover_subnets()

    # ------------------------------------------------------------------ #
    # Public: Scale Up                                                     #
    # ------------------------------------------------------------------ #

    def scale_up(
        self,
        nodes_to_add: int,
        reason: str,
        state_manager=None,
    ) -> Dict:
        """
        Launch `nodes_to_add` new EC2 worker instances.

        Returns:
            {success, instance_ids, spot_count, ondemand_count, node_join_latency_ms}
        """
        logger.info(f"Scaling up: adding {nodes_to_add} node(s). Reason: {reason}")

        if not self.available_subnets:
            logger.error("No worker subnets available for scale-up")
            return {"success": False, "instance_ids": [], "spot_count": 0, "ondemand_count": 0}

        instance_ids: List[str] = []
        spot_launched = 0
        ondemand_launched = 0
        existing_workers = self._get_worker_instances()

        # Track subnets used in THIS call for round-robin balancing across iterations
        used_subnet_records: List[Dict] = []

        for _ in range(nodes_to_add):
            use_spot = self._should_use_spot(spot_launched, ondemand_launched, nodes_to_add)
            subnet_id = select_subnet_for_new_instance(
                existing_workers + used_subnet_records,
                self.available_subnets,
            )

            try:
                instance_id = self._launch_instance(use_spot, subnet_id)
                instance_ids.append(instance_id)
                used_subnet_records.append({"SubnetId": subnet_id})
                if use_spot:
                    spot_launched += 1
                else:
                    ondemand_launched += 1
                logger.info(f"Launched {'spot' if use_spot else 'on-demand'} instance {instance_id} in {subnet_id}")
            except Exception as e:
                if use_spot:
                    logger.warning(f"Spot launch failed ({e}), falling back to on-demand")
                    try:
                        instance_id = self._launch_instance(False, subnet_id)
                        instance_ids.append(instance_id)
                        used_subnet_records.append({"SubnetId": subnet_id})
                        ondemand_launched += 1
                        logger.info(f"Launched on-demand (fallback) instance {instance_id} in {subnet_id}")
                    except Exception as e2:
                        logger.error(f"On-demand fallback also failed: {e2}")
                else:
                    logger.error(f"On-demand launch failed: {e}")

        if not instance_ids:
            return {"success": False, "instance_ids": [], "spot_count": 0, "ondemand_count": 0}

        # Store pending scale-up in DynamoDB for verification on next invocation
        if state_manager:
            state_manager.store_pending_scale_up(instance_ids, int(time.time()))

        return {
            "success": True,
            "instance_ids": instance_ids,
            "spot_count": spot_launched,
            "ondemand_count": ondemand_launched,
            "node_join_latency_ms": None,  # Async — verified on next invocation
        }

    # ------------------------------------------------------------------ #
    # Public: Scale Down                                                   #
    # ------------------------------------------------------------------ #

    def scale_down(
        self,
        nodes_to_remove: int,
        reason: str,
        state_manager=None,
    ) -> Dict:
        """
        Initiate graceful scale-down by:
        1. Selecting the safest worker node(s) to remove
        2. Sending `kubectl drain` via SSM Run Command to master (non-blocking)
        3. Storing drain state in DynamoDB for completion on next Lambda invocation

        Returns:
            {success, instance_ids}
        """
        logger.info(f"Scaling down: removing {nodes_to_remove} node(s). Reason: {reason}")

        workers = self._get_worker_instances()
        if not workers:
            logger.warning("No worker instances found to remove")
            return {"success": False, "instance_ids": []}

        targets = self._select_instances_for_termination(workers, nodes_to_remove)
        master_instance_id = self._get_master_instance_id()

        draining = []
        for instance in targets:
            instance_id = instance["InstanceId"]
            node_name = instance.get("PrivateDnsName", instance_id).split(".")[0]

            # Guard: skip nodes hosting StatefulSet pods, single-replica deployment pods, or kube-system non-DaemonSet pods
            has_critical, critical_reason = self._check_critical_pods(master_instance_id, node_name)
            if has_critical:
                logger.warning(f"Skipping drain for {instance_id} (node: {node_name}) — critical pods present: {critical_reason}")
                continue

            try:
                command_id = self._send_drain_command(master_instance_id, node_name)
                draining.append({
                    "instance_id": instance_id,
                    "node_name": node_name,
                    "command_id": command_id,
                    "master_instance_id": master_instance_id,
                    "start_time": int(time.time()),
                })
                logger.info(f"Drain initiated for {instance_id} (node: {node_name}, ssm: {command_id})")
            except Exception as e:
                logger.error(f"Failed to initiate drain for {instance_id}: {e}")

        if draining and state_manager:
            state_manager.store_drain_state(draining)

        return {
            "success": len(draining) > 0,
            "instance_ids": [d["instance_id"] for d in draining],
        }

    # ------------------------------------------------------------------ #
    # Public: Complete Pending Drains                                      #
    # ------------------------------------------------------------------ #

    def complete_pending_drains(self, state_manager) -> List[str]:
        """
        Check status of previously initiated SSM drain commands.
        Terminates instance + deletes K3s node if drain succeeded.

        Returns:
            List of terminated instance IDs
        """
        pending = state_manager.get_pending_drains()
        if not pending:
            return []

        terminated = []
        for drain in pending:
            instance_id = drain["instance_id"]
            node_name = drain["node_name"]
            command_id = drain["command_id"]
            master_instance_id = drain["master_instance_id"]
            start_time = drain["start_time"]

            elapsed = int(time.time()) - start_time

            try:
                status, output = self._get_ssm_command_status(master_instance_id, command_id)
                logger.info(f"Drain status for {instance_id}: {status} (elapsed: {elapsed}s)")

                if status == "Success" and "drained" in output.lower():
                    # Drain complete — delete node from cluster then terminate EC2
                    self._delete_k3s_node(master_instance_id, node_name)
                    self.ec2.terminate_instances(InstanceIds=[instance_id])
                    state_manager.clear_drain_instance(instance_id)
                    terminated.append(instance_id)
                    logger.info(f"Terminated {instance_id} after successful drain")

                elif status in ("Failed", "TimedOut", "Cancelled", "Cancelling") or elapsed > DRAIN_TIMEOUT_SECONDS:
                    # Drain failed or timed out — skip termination to protect workloads
                    logger.error(f"Drain failed for {instance_id} (status={status}). Skipping termination.")
                    state_manager.clear_drain_instance(instance_id)

                else:
                    logger.info(f"Drain still in progress for {instance_id} (status={status})")

            except Exception as e:
                logger.error(f"Error checking drain status for {instance_id}: {e}")

        return terminated

    # ------------------------------------------------------------------ #
    # Public: Check Pending Scale-Ups                                      #
    # ------------------------------------------------------------------ #

    def check_pending_scale_ups(self, state_manager) -> List[str]:
        """
        Verify that pending scale-up instances have joined the K3s cluster
        and reached Ready state. Clears confirmed instances from DynamoDB.

        Returns:
            List of instance IDs confirmed as Ready
        """
        pending = state_manager.get_pending_scale_ups()
        if not pending:
            return []

        confirmed = []
        now = int(time.time())

        for item in pending:
            instance_id = item["instance_id"]
            launch_time = item["launch_time"]
            elapsed = now - launch_time

            try:
                response = self.ec2.describe_instances(InstanceIds=[instance_id])
                reservations = response.get("Reservations", [])
                if not reservations:
                    if elapsed > NODE_READY_TIMEOUT_SECONDS:
                        logger.warning(f"Instance {instance_id} not found after {elapsed}s, removing from pending")
                        state_manager.clear_pending_scale_up(instance_id)
                    continue

                instance = reservations[0]["Instances"][0]
                state = instance["State"]["Name"]

                if state == "running":
                    confirmed.append(instance_id)
                    state_manager.clear_pending_scale_up(instance_id)
                    logger.info(f"Instance {instance_id} confirmed running ({elapsed}s after launch)")
                elif state in ("terminated", "shutting-down"):
                    logger.warning(f"Instance {instance_id} unexpectedly in state {state}")
                    state_manager.clear_pending_scale_up(instance_id)
                elif elapsed > NODE_READY_TIMEOUT_SECONDS:
                    logger.warning(f"Instance {instance_id} still {state} after {elapsed}s, abandoning wait")
                    state_manager.clear_pending_scale_up(instance_id)
                else:
                    logger.info(f"Instance {instance_id} is {state}, waiting ({elapsed}s)")

            except Exception as e:
                logger.error(f"Error checking scale-up status for {instance_id}: {e}")

        return confirmed

    # ------------------------------------------------------------------ #
    # Public: Spot Interruption Handler                                    #
    # ------------------------------------------------------------------ #

    def handle_spot_interruption_event(self, instance_id: str) -> Dict:
        """
        Handle EC2 Spot Interruption Warning event.
        Drains the node immediately via SSM and returns status.

        Returns:
            {success, action, node, reason}
        """
        logger.warning(f"Handling spot interruption for {instance_id}")

        try:
            response = self.ec2.describe_instances(InstanceIds=[instance_id])
            reservations = response.get("Reservations", [])
            if not reservations:
                return {"success": False, "action": "skipped", "node": instance_id, "reason": "Instance not found"}

            instance = reservations[0]["Instances"][0]
            node_name = instance.get("PrivateDnsName", instance_id).split(".")[0]

            # Tag so future scans skip this instance
            self.ec2.create_tags(
                Resources=[instance_id],
                Tags=[{"Key": "SpotInterruption", "Value": "true"}],
            )

            master_instance_id = self._get_master_instance_id()
            self._send_drain_command(master_instance_id, node_name)

            return {
                "success": True,
                "action": "drain_initiated",
                "node": node_name,
                "reason": "Spot interruption warning — drain started",
            }

        except Exception as e:
            logger.error(f"Error handling spot interruption for {instance_id}: {e}")
            return {"success": False, "action": "failed", "node": instance_id, "reason": str(e)}

    # ------------------------------------------------------------------ #
    # Private: Helpers                                                     #
    # ------------------------------------------------------------------ #

    def _launch_instance(self, use_spot: bool, subnet_id: str) -> str:
        """Launch a single EC2 instance. Returns instance ID."""
        template_id = self.worker_spot_template_id if use_spot and self.worker_spot_template_id else self.worker_template_id
        cluster_id = os.environ.get("CLUSTER_ID", "k3s")
        instance_type = "spot" if use_spot else "ondemand"
        # Unique name: <cluster>-worker-<type>-<epoch-seconds>
        name = f"{cluster_id}-worker-{instance_type}-{int(time.time())}"

        response = self.ec2.run_instances(
            LaunchTemplate={"LaunchTemplateId": template_id},
            MinCount=1,
            MaxCount=1,
            SubnetId=subnet_id,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": name},
                        {"Key": "Role", "Value": "k3s-worker"},
                        {"Key": "Project", "Value": "node-fleet"},
                        {"Key": "ManagedBy", "Value": "autoscaler"},
                        {"Key": "InstanceType", "Value": instance_type},
                        {"Key": "LaunchedAt", "Value": str(int(time.time()))},
                    ],
                }
            ],
        )
        return response["Instances"][0]["InstanceId"]

    def _should_use_spot(self, spot_so_far: int, ondemand_so_far: int, total: int) -> bool:
        """Decide if the next instance should be spot based on target percentage."""
        if not self.worker_spot_template_id:
            return False
        target_spot = round(total * self.spot_percentage / 100)
        return spot_so_far < target_spot

    def _get_worker_instances(self) -> List[Dict]:
        """Return all running K3s worker instances."""
        response = self.ec2.describe_instances(
            Filters=[
                {"Name": "tag:Role", "Values": ["k3s-worker"]},
                {"Name": "instance-state-name", "Values": ["running", "pending"]},
            ]
        )
        instances = []
        for reservation in response.get("Reservations", []):
            instances.extend(reservation.get("Instances", []))
        return instances

    def _discover_subnets(self) -> List[str]:
        """
        Discover subnets tagged for worker placement and cache as self.available_subnets.
        Returns an empty list (never raises) if no subnets are found.
        """
        try:
            response = self.ec2.describe_subnets(
                Filters=[{"Name": "tag:Project", "Values": ["node-fleet"]}]
            )
            subnets = [s["SubnetId"] for s in response.get("Subnets", [])]
        except Exception as e:
            logger.warning(f"Failed to discover subnets via tag: {e}")
            subnets = []

        if not subnets:
            # Fallback: read from environment variable (set by Pulumi via Lambda env)
            env_subnets = os.environ.get("WORKER_SUBNETS", "")
            subnets = [s.strip() for s in env_subnets.split(",") if s.strip()]

        if not subnets:
            logger.warning("No worker subnets found. Tag subnets with Project=node-fleet or set WORKER_SUBNETS env var.")

        return subnets

    def _select_instances_for_termination(self, instances: List[Dict], count: int) -> List[Dict]:
        """
        Select `count` instances for termination using weighted scoring:
        - Prefer spot instances (cheaper, already interruptible)
        - Prefer instances with fewer pod-hosting indicators
        """
        def score(instance):
            # Lower score = preferred for removal
            is_spot = 1 if instance.get("InstanceLifecycle") == "spot" else 0
            launch_time = instance.get("LaunchTime")
            # Prefer newer instances (less established) — epoch seconds as tie-breaker
            age = 0
            if launch_time:
                try:
                    age = int(launch_time.timestamp()) if hasattr(launch_time, "timestamp") else 0
                except Exception:
                    age = 0
            # Lower score = remove first: spot instances score -1, on-demand score 0
            return (0 if is_spot else 1, -age)

        sorted_instances = sorted(instances, key=score)
        return sorted_instances[:count]

    def _get_master_instance_id(self) -> str:
        """Resolve master EC2 instance ID dynamically via EC2 tag (never hardcoded)."""
        response = self.ec2.describe_instances(
            Filters=[
                {"Name": "tag:Role", "Values": ["k3s-master"]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ]
        )
        reservations = response.get("Reservations", [])
        if not reservations or not reservations[0].get("Instances"):
            raise RuntimeError("K3s master instance not found via tag Role=k3s-master")
        return reservations[0]["Instances"][0]["InstanceId"]

    def _check_critical_pods(self, master_instance_id: str, node_name: str) -> Tuple[bool, str]:
        """
        Check if node hosts critical pods that must not be drained.
        Critical = StatefulSet pods, single-replica Deployment pods, kube-system non-DaemonSet pods.
        Fail-safe: returns (True, reason) if check fails, preventing unsafe drain.

        Returns:
            (has_critical, reason) — True means skip this node.
        """
        check_cmd = (
            f"export KUBECONFIG=/etc/rancher/k3s/k3s.yaml; "
            f"NODE='{node_name}'; "
            # StatefulSet pods on this node
            "SS=$(kubectl get pods --all-namespaces --field-selector spec.nodeName=$NODE "
            "-o jsonpath='{range .items[*]}{.metadata.ownerReferences[0].kind}{\"\\n\"}{end}' "
            "2>/dev/null | grep -c '^StatefulSet$' || echo 0); "
            # kube-system non-DaemonSet pods on this node (CoreDNS, metrics-server)
            "KS=$(kubectl get pods -n kube-system --field-selector spec.nodeName=$NODE "
            "-o jsonpath='{range .items[*]}{.metadata.ownerReferences[0].kind}{\"\\n\"}{end}' "
            "2>/dev/null | grep -vc '^DaemonSet$' || echo 0); "
            # Single-replica Deployments cluster-wide (any namespace)
            "SR=$(kubectl get deployments --all-namespaces "
            "-o jsonpath='{range .items[*]}{.spec.replicas}{\"\\n\"}{end}' "
            "2>/dev/null | grep -c '^1$' || echo 0); "
            "[ $((${SS:-0}+${KS:-0}+${SR:-0})) -gt 0 ] "
            "&& echo \"CRITICAL_PODS_FOUND:statefulset=${SS},kube-system-non-ds=${KS},single-replica-deployments=${SR}\" "
            "|| echo SAFE_TO_DRAIN"
        )
        try:
            resp = self.ssm.send_command(
                InstanceIds=[master_instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [check_cmd]},
                Comment=f"K3s autoscaler: critical pod check {node_name}",
                TimeoutSeconds=60,
            )
            command_id = resp["Command"]["CommandId"]
            start = time.time()
            while time.time() - start < 30:
                status, output = self._get_ssm_command_status(master_instance_id, command_id)
                if status in ("Success", "Failed", "TimedOut", "Cancelled", "Cancelling"):
                    break
                time.sleep(2)
            if "CRITICAL_PODS_FOUND" in output:
                reason = output.strip()
                logger.warning(f"Critical pods on {node_name}: {reason}")
                return True, reason
            return False, ""
        except Exception as e:
            logger.error(f"Critical pod check failed for {node_name}: {e}. Skipping drain for safety.")
            return True, f"check_error:{e}"

    def _send_drain_command(self, master_instance_id: str, node_name: str) -> str:
        """
        Send kubectl drain command to master via SSM Run Command.
        Returns SSM command ID (non-blocking).
        """
        drain_cmd = (
            f"kubectl drain {node_name} "
            f"--ignore-daemonsets --delete-emptydir-data --force --timeout=300s"
        )
        response = self.ssm.send_command(
            InstanceIds=[master_instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [f"export KUBECONFIG=/etc/rancher/k3s/k3s.yaml && {drain_cmd}"]},
            Comment=f"K3s autoscaler: drain {node_name}",
            TimeoutSeconds=360,
        )
        return response["Command"]["CommandId"]

    def _get_ssm_command_status(self, master_instance_id: str, command_id: str):
        """
        Poll SSM for command result.
        Returns (status_string, stdout_output).
        """
        try:
            response = self.ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=master_instance_id,
            )
        except self.ssm.exceptions.InvocationDoesNotExist:
            return "Pending", ""
        status = response.get("Status", "Pending")
        output = response.get("StandardOutputContent", "") + response.get("StandardErrorContent", "")
        return status, output

    def _delete_k3s_node(self, master_instance_id: str, node_name: str) -> None:
        """Delete node from K3s cluster to prevent ghost nodes."""
        try:
            self.ssm.send_command(
                InstanceIds=[master_instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [
                    f"export KUBECONFIG=/etc/rancher/k3s/k3s.yaml && kubectl delete node {node_name} --ignore-not-found"
                ]},
                Comment=f"K3s autoscaler: delete node {node_name}",
                TimeoutSeconds=60,
            )
        except Exception as e:
            logger.warning(f"Failed to delete K3s node object for {node_name}: {e}")
