    def _get_node_pod_info(self) -> Dict[str, Dict]:
        """
        Get pod count and StatefulSet presence per node utilizing SSH for reliability
        
        Returns:
            Dict mapping node name to {'count': int, 'has_sts': bool, 'has_critical': bool, 'is_single_replica': bool}
        """
        try:
            # Execute kubectl get pods -A -o json via SSH
            cmd = "sudo k3s kubectl get pods -A -o json"
            import paramiko
            import io
            import json
            
            # Re-implementing simplified SSH exec here to capture output directly
            # or we could reuse _execute_master_command if we modified it to return output
            # For brevity and safety, let's implement the specific logic here or modify _execute
            
            # Reuse _get_ssh_key
            private_key_str = self._get_ssh_key()
            if not private_key_str:
                logger.error("No SSH key available for pod info")
                return {}
                
            private_key = paramiko.RSAKey.from_private_key(io.StringIO(private_key_str))
            
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            master_ip = "10.0.1.147"
            client.connect(hostname=master_ip, username="ubuntu", pkey=private_key, timeout=10)
            
            stdin, stdout, stderr = client.exec_command(cmd)
            exit_status = stdout.channel.recv_exit_status()
            out_str = stdout.read().decode().strip()
            client.close()
            
            if exit_status != 0:
                logger.error(f"Failed to get pods via SSH: {stderr.read().decode()}")
                return {}
                
            try:
                pod_list = json.loads(out_str)
            except json.JSONDecodeError:
                logger.error("Failed to decode JSON from pod list")
                return {}
            
            node_info = defaultdict(lambda: {'count': 0, 'has_sts': False, 'has_critical': False, 'is_single_replica': False})
            
            # We also need ReplicaSet info for single-replica check
            # Executing a second command to get ReplicaSets
            # Optimized: We'll skip the single-replica deep check via SSH for now or do a second call?
            # Let's do a second call for ReplicaSets to be thorough
            
            client.connect(hostname=master_ip, username="ubuntu", pkey=private_key, timeout=10)
            rs_cmd = "sudo k3s kubectl get replicasets -A -o json"
            stdin, stdout, stderr = client.exec_command(rs_cmd)
            rs_out = stdout.read().decode().strip()
            client.close()
            
            rs_map = {} # namespace/name -> replicas
            try:
                rs_list = json.loads(rs_out)
                for rs in rs_list.get('items', []):
                    key = f"{rs['metadata']['namespace']}/{rs['metadata']['name']}"
                    rs_map[key] = rs.get('spec', {}).get('replicas', 1)
            except:
                logger.warning("Failed to parse ReplicaSets via SSH, assuming safe defaults")
            
            for pod in pod_list.get('items', []):
                status = pod.get('status', {})
                if status.get('phase') in ['Succeeded', 'Failed']:
                    continue
                
                spec = pod.get('spec', {})
                node_name = spec.get('nodeName')
                if not node_name:
                    continue
                
                metadata = pod.get('metadata', {})
                namespace = metadata.get('namespace', 'default')
                
                # 1. Critical Pods
                if namespace == 'kube-system':
                    is_daemonset = False
                    owners = metadata.get('ownerReferences', [])
                    for owner in owners:
                        if owner.get('kind') == 'DaemonSet':
                            is_daemonset = True
                            break
                    
                    annotations = metadata.get('annotations', {})
                    is_static = 'kubernetes.io/config.mirror' in annotations
                    
                    if not is_daemonset and not is_static:
                        node_info[node_name]['has_critical'] = True
                    continue
                
                node_info[node_name]['count'] += 1
                
                # 2. StatefulSets & 3. Single Replica
                owners = metadata.get('ownerReferences', [])
                for owner in owners:
                    kind = owner.get('kind')
                    if kind == 'StatefulSet':
                        node_info[node_name]['has_sts'] = True
                    
                    elif kind == 'ReplicaSet':
                        rs_name = owner.get('name')
                        key = f"{namespace}/{rs_name}"
                        if rs_map.get(key) == 1:
                            node_info[node_name]['is_single_replica'] = True
            
            logger.info(f"SSH Retrieved Node Pod Info: {json.dumps(node_info)}")
            return node_info
            
        except Exception as e:
            logger.error(f"Error getting node pod info via SSH: {e}")
            return {}
