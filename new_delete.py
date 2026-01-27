    def _delete_node(self, node_name: str) -> bool:
        """Delete node using SSH to master"""
        logger.info(f"Deleting node {node_name} object via SSH...")
        cmd = f"sudo k3s kubectl delete node {node_name} --ignore-not-found"
        return self._execute_master_command(cmd)
