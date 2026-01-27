    def _get_ssh_key(self) -> str:
        """Retrieve SSH key from Secrets Manager"""
        try:
            logger.info("Retrieving SSH key from Secrets Manager...")
            sm = boto3.client('secretsmanager')
            response = sm.get_secret_value(SecretId='node-fleet/ssh-key')
            return response['SecretString']
        except Exception as e:
            logger.error(f"Failed to get SSH key: {e}")
            return None

    def _execute_master_command(self, command: str) -> bool:
        """Execute command on master node via SSH"""
        import paramiko
        import io
        
        try:
            # Get SSH key
            private_key_str = self._get_ssh_key()
            if not private_key_str:
                return False
                
            private_key = paramiko.RSAKey.from_private_key(io.StringIO(private_key_str))
            
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Connect to Master Private IP
            master_ip = "10.0.1.147"
            logger.info(f"SSH Connecting to master {master_ip}...")
            client.connect(hostname=master_ip, username="ubuntu", pkey=private_key, timeout=10)
            
            logger.info(f"Executing: {command}")
            stdin, stdout, stderr = client.exec_command(command)
            exit_status = stdout.channel.recv_exit_status()
            
            out_str = stdout.read().decode().strip()
            err_str = stderr.read().decode().strip()
            
            if exit_status == 0:
                logger.info(f"Command success: {out_str}")
                client.close()
                return True
            else:
                logger.error(f"Command failed (exit {exit_status}): {err_str}")
                client.close()
                return False
                
        except Exception as e:
            logger.error(f"SSH Execution Error: {e}")
            return False

    def _drain_node(self, node_name: str, timeout: int = 300) -> bool:
        """Drain node using SSH to master (more reliable than remote API)"""
        logger.info(f"Draining node {node_name} via SSH...")
        cmd = f"sudo k3s kubectl drain {node_name} --ignore-daemonsets --delete-emptydir-data --force --timeout={timeout}s"
        return self._execute_master_command(cmd)
