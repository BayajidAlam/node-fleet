---
name: ansible-playbooks
version: 1.0.0
description: Best practices for writing and debugging Ansible playbooks in VisionSync. Covers inventory management, SSH jump host configuration, idempotency, Jinja2 templates, and deployment patterns for EC2-based MongoDB, Redis, and Node.js services.
---

> 📋 **Always read [`.agents/CONTEXT.md`](./../CONTEXT.md) first** — it contains the full project architecture, env vars, API routes, and infrastructure details for VisionSync.

# Ansible Playbooks Skill

## When to Use This Skill

Invoke this skill when:
- Writing or modifying Ansible playbooks in `ansible/`
- Debugging SSH connection failures to private subnet instances
- Working with Jinja2 templates (`inventory.j2`, `production-env.j2`)
- Setting up or repairing the MongoDB replica set
- Deploying Docker containers to EC2 instances via Ansible
- Managing dynamic inventory from Pulumi outputs

---

## Practices

### 1. Always SSH via Bastion for Private Instances

**Why**: Backend, MongoDB, and Redis instances are in private subnets with no direct internet access. All SSH must jump through the Bastion host in the public subnet.

**Detection signals**:
- `ssh: connect to host <private-ip> port 22: Connection timed out`
- Playbook hangs indefinitely when targeting backend/database hosts
- `unreachable` errors in Ansible output for private hosts

**Wrong**:
```ini
# hosts.ini - direct connection to private IP won't work
[backend]
10.10.3.10 ansible_user=ubuntu ansible_ssh_private_key_file=~/.ssh/vision-sync-backend
```

**Right**:
```ini
# hosts.ini - use ProxyJump via bastion
[bastion]
<BASTION_PUBLIC_IP> ansible_user=ubuntu ansible_ssh_private_key_file=~/.ssh/vision-sync-backend

[backend]
10.10.3.10 ansible_user=ubuntu ansible_ssh_private_key_file=~/.ssh/vision-sync-backend
             ansible_ssh_common_args='-o StrictHostKeyChecking=no -o ProxyJump=ubuntu@<BASTION_PUBLIC_IP>'

[mongodb]
10.10.4.10 ansible_user=ubuntu ansible_ssh_private_key_file=~/.ssh/vision-sync-backend
            ansible_ssh_common_args='-o StrictHostKeyChecking=no -o ProxyJump=ubuntu@<BASTION_PUBLIC_IP>'
```

**Or in `ansible.cfg`**:
```ini
[ssh_connection]
ssh_args = -o ControlMaster=auto -o ControlPersist=60s -o StrictHostKeyChecking=no
```

---

### 2. Make Every Task Idempotent

**Why**: Ansible playbooks should be safe to re-run without side effects. Non-idempotent tasks (like `command: mongod --replSet init`) will fail or corrupt state on the second run.

**Detection signals**:
- Task fails on second run with "already exists" errors
- Database gets re-initialized when rerunning the setup playbook
- Docker containers get duplicated

**Wrong**:
```yaml
- name: Initialize MongoDB replica set
  ansible.builtin.command: mongosh --eval "rs.initiate()"
  # WRONG: runs every time, fails if already initiated
```

**Right**:
```yaml
- name: Check replica set status
  ansible.builtin.command: mongosh --eval "rs.status().ok"
  register: rs_status
  ignore_errors: yes

- name: Initialize MongoDB replica set
  ansible.builtin.command: mongosh --eval "rs.initiate({ _id: 'rs0', members: [...] })"
  when: rs_status.stdout != "1"
  # Only runs if replica set isn't already initialized
```

---

### 3. Use Jinja2 Templates for Environment Files

**Why**: Hardcoding IPs or URLs in playbooks breaks portability. The `production-env.j2` template + Pulumi output variables pattern lets you generate `.env` files dynamically at deploy time.

**Detection signals**:
- Backend `.env` has hardcoded IPs that break after infrastructure changes
- `make update-env` doesn't reflect new Pulumi outputs

**Right**:
```jinja2
{# ansible/templates/production-env.j2 #}
AWS_REGION={{ aws_region }}
S3_BUCKET_RAW={{ s3_bucket_raw }}
S3_BUCKET_PROCESSED={{ s3_bucket_processed }}
SQS_QUEUE_URL={{ sqs_queue_url }}
CLOUDFRONT_DOMAIN={{ cloudfront_domain }}
MONGODB_URI=mongodb://{{ mongodb_primary_ip }}:27017,{{ mongodb_secondary1_ip }}:27017,{{ mongodb_secondary2_ip }}:27017/vision-sync?replicaSet=rs0
REDIS_URL=redis://{{ redis_ip }}:6379
PORT=5000
NODE_ENV=production
```

```yaml
- name: Generate production .env file
  ansible.builtin.template:
    src: templates/production-env.j2
    dest: /app/vision-sync/.env
    owner: ubuntu
    mode: '0600'    # Restrict permissions on .env
  vars:
    aws_region: "ap-southeast-1"
    mongodb_primary_ip: "{{ hostvars['mongodb-primary']['ansible_host'] }}"
```

---

### 4. Use `block/rescue/always` for Critical Operations

**Why**: Database setup, Docker pulls, and replica set initialization can fail in ways that leave systems in a broken intermediate state. `block/rescue/always` ensures cleanup happens and meaningful error messages are logged.

**Detection signals**:
- MongoDB stops after a failed partial initialization
- Docker containers left in `Exited` state after a failed deploy

**Right**:
```yaml
- name: Deploy backend container
  block:
    - name: Pull latest image from ECR
      ansible.builtin.command: >
        docker pull {{ ecr_url }}/vision-sync-backend:latest

    - name: Stop existing container
      ansible.builtin.command: docker stop vision-sync-backend
      ignore_errors: yes

    - name: Run new container
      ansible.builtin.command: >
        docker run -d --name vision-sync-backend
        --restart always
        --env-file /app/vision-sync/.env
        -p 5000:5000
        {{ ecr_url }}/vision-sync-backend:latest

  rescue:
    - name: Log failure
      ansible.builtin.debug:
        msg: "Backend deployment failed. Check ECR pull permissions and Docker daemon."

    - name: Restart old container if exists
      ansible.builtin.command: docker start vision-sync-backend
      ignore_errors: yes

  always:
    - name: Verify container is running
      ansible.builtin.command: docker ps --filter name=vision-sync-backend
      register: container_status
    - ansible.builtin.debug: var=container_status.stdout
```

---

### 5. Authenticate with ECR Before Docker Pull

**Why**: ECR requires a temporary auth token (valid 12 hours). Pulling without auth causes `denied: Your authorization token has expired` after 12 hours.

**Detection signals**:
- `docker pull` fails with `no basic auth credentials`
- Deployments succeed initially but fail the next day

**Right**:
```yaml
- name: Authenticate Docker with ECR
  ansible.builtin.shell: >
    aws ecr get-login-password --region ap-southeast-1 |
    docker login --username AWS --password-stdin {{ ecr_url }}
  environment:
    AWS_DEFAULT_REGION: ap-southeast-1
  # The EC2 instance role must have ecr:GetAuthorizationToken permission
```

---

### 6. Always Check MongoDB Replica Set After Setup

**Why**: MongoDB replica set configuration silently fails if member IPs are wrong, if DNS resolution fails, or if firewall rules block inter-member communication on port 27017.

**Detection signals**:
- `rs.status()` shows members in `STARTUP2` or `UNKNOWN` state
- Application gets `MongoServerError: not primary` on all members

**Right**:
```yaml
- name: Verify replica set members are healthy
  ansible.builtin.command: >
    mongosh --quiet --eval
    "JSON.stringify(rs.status().members.map(m => ({ name: m.name, state: m.stateStr })))"
  register: rs_members
  retries: 5
  delay: 10
  until: "'PRIMARY' in rs_members.stdout"

- name: Show replica set status
  ansible.builtin.debug:
    msg: "{{ rs_members.stdout }}"
```

---

## Quick Reference

| Issue | Likely Cause | Fix |
|-------|-------------|-----|
| SSH timeout to private host | Missing ProxyJump | Add `ansible_ssh_common_args` with ProxyJump |
| Playbook fails on re-run | Non-idempotent tasks | Add `when:` guards with status checks |
| `.env` has wrong IPs | Hardcoded values | Use `production-env.j2` + Pulumi vars |
| ECR pull fails | Auth token expired | Run `aws ecr get-login-password` first |
| Replica set stuck in STARTUP2 | Port 27017 blocked | Check security group allows MongoDB between private subnets |
| Container exits immediately | Missing env vars | Check `.env` file has all required variables |

## VisionSync Specific Context

- **SSH key path**: `~/.ssh/vision-sync-backend`  
- **Default user**: `ubuntu` on all EC2 instances
- **MongoDB port**: `27017` — must be open between all 3 MongoDB instances
- **Docker registry**: AWS ECR at `<account>.dkr.ecr.ap-southeast-1.amazonaws.com`
- **Backend container name**: `vision-sync-backend` (used in `docker stop/start`)
- **Replica set name**: `rs0`
- **MongoDB primary**: `mongodbInstance1` (first instance created in Pulumi)
- **Redis container**: runs on port `6379` as `vision-sync-redis`
