---
name: code-reviewer
version: 1.0.0
description: Code review skill for VisionSync. Covers TypeScript patterns, Express/Mongoose anti-patterns, Pulumi IaC correctness, security vulnerabilities, AWS SDK v3 usage, and general code quality across all project layers.
---

> 📋 **Always read [`.agents/CONTEXT.md`](./../CONTEXT.md) first** — it contains the full project architecture, env vars, API routes, and infrastructure details for VisionSync.

# Code Reviewer Skill

## When to Use This Skill

Invoke this skill when:
- Reviewing a pull request or code changes across any part of the project
- Asked to "review", "check", or "audit" code in `server/`, `container/`, `lambda/`, `IaC/`, or `ansible/`
- Validating that new code follows VisionSync conventions
- Catching security issues before deployment
- Reviewing Pulumi infrastructure changes before `pulumi up`

---

## Review Checklist by Layer

### 🖥️ Backend (`server/src/`)

#### TypeScript & Express
- [ ] All route handlers typed with `Request<Params, ResBody, ReqBody>` — no implicit `any`
- [ ] All `async` route handlers wrapped in `try/catch` or use an error middleware
- [ ] No `console.log` in production paths — use a structured logger
- [ ] `req.ip` used for rate limiting only after `app.set('trust proxy', 1)` is set
- [ ] No hardcoded secrets or URLs — all from `process.env`
- [ ] All `process.env` accesses validated at startup (fail fast if missing)

**Example red flag:**
```typescript
// ❌ Untyped, no error handling, hardcoded secret
app.post('/upload', async (req, res) => {
  const data = await s3.upload({ Bucket: 'my-raw-bucket-hardcoded', ... });
  res.json(data);
});
```

#### Mongoose
- [ ] Schemas have `timestamps: true` for audit trails
- [ ] Sensitive fields (passwords) use `select: false` to exclude from queries
- [ ] Instance methods declared in the TypeScript interface, not just on the schema
- [ ] No `Model.find()` without a query filter (full collection scans)
- [ ] Mongoose connections use replica set URI in production

#### Socket.IO
- [ ] Events emitted to specific rooms (`io.to(videoId).emit(...)`) — not broadcast to all
- [ ] No sensitive data emitted over Socket.IO without auth validation
- [ ] Socket.IO adapter configured with Redis for multi-instance backend

---

### 🎬 Container (`container/src/`)

- [ ] FFmpeg output uses `use_timeline 1` and `use_template 1` for valid DASH
- [ ] `SIGTERM` handler present — sends failure webhook before exiting (Spot interruption)
- [ ] S3 uploads use multipart or concurrent upload for performance
- [ ] Manifest validated before webhook is sent
- [ ] No `process.exit(1)` without first sending a failure webhook to the backend

**Example red flag:**
```typescript
// ❌ No SIGTERM handler + exits without notifying backend
ffmpeg(...).on('error', () => process.exit(1));
```

---

### ⚡ Lambda (`lambda/`)

- [ ] SQS message body parsed with `JSON.parse` inside a `try/catch`
- [ ] ECS task launch errors are caught — failed tasks shouldn't crash the Lambda silently
- [ ] `fileSize` used correctly for Spot/Regular selection (< 1GB threshold)
- [ ] All required ECS env vars (`VIDEO_ID`, `S3_KEY`, `WEBHOOK_URL`, `FFMPEG_PRESET`) are set
- [ ] SQS message deleted only **after** successfully launching ECS task (not before)

---

### 🏗️ IaC (`IaC/src/`)

- [ ] No `new Resource()` inside `.apply()` callbacks
- [ ] All outputs passed directly as inputs — no manual `.apply()` unwrapping
- [ ] Secrets stored with `config.requireSecret()` — not `config.require()`
- [ ] Every new compute resource has a CloudWatch log group
- [ ] Every new IAM role uses least-privilege (specific actions + specific resource ARNs)
- [ ] Renamed/moved resources have `aliases` to prevent destroy + recreate
- [ ] `pulumi preview` output reviewed before `pulumi up`

**Example red flag:**
```typescript
// ❌ Resource created inside apply()
bucket.id.apply(id => {
  new aws.s3.BucketPolicy('policy', { bucket: id, ... });
});
```

---

### 📋 Ansible (`ansible/`)

- [ ] All private host groups use `ansible_ssh_common_args` with `ProxyJump` via bastion
- [ ] Tasks that should be idempotent use `when:` guards (check state before acting)
- [ ] `docker pull` preceded by ECR login task (`aws ecr get-login-password ...`)
- [ ] No hardcoded IPs in playbooks — all from inventory variables
- [ ] Sensitive values (passwords, tokens) use `ansible-vault` or `vars_prompt`
- [ ] `block/rescue/always` used for critical operations (DB init, container deploy)

---

### 🔐 Security (All Layers)

- [ ] No credentials in source code, `.env` files committed to Git, or logs
- [ ] S3 bucket policies deny public access — all video delivery goes through CloudFront
- [ ] IAM roles are service-specific — no shared "god role" across services
- [ ] Security groups follow least-privilege: only required ports, from specific SGs (not `0.0.0.0/0`)
- [ ] Presigned S3 URLs expire in ≤ 15 minutes
- [ ] API inputs validated with `express-validator` before processing
- [ ] Rate limiting applied to all public-facing endpoints

---

## Severity Levels

When flagging issues, use these labels:

| Severity | Label | Examples |
|---|---|---|
| 🔴 **Critical** | `[SECURITY]` / `[DATA LOSS]` | Hardcoded credentials, no input validation, no DLQ |
| 🟠 **High** | `[BUG]` | Missing SIGTERM handler, broken DASH manifest, replica set not in URI |
| 🟡 **Medium** | `[PERF]` / `[RELIABILITY]` | Sequential S3 uploads, missing `trust proxy`, full collection scan |
| 🟢 **Low** | `[STYLE]` / `[CONVENTION]` | Missing `timestamps`, untyped Express handler, `console.log` in prod |

---

## Review Output Format

When completing a review, output findings in this structure:

```
## Code Review: <filename or PR description>

### 🔴 Critical
- [SECURITY] Line 42: Hardcoded S3 bucket name — use `process.env.S3_BUCKET_RAW`

### 🟠 High
- [BUG] Line 87: No SIGTERM handler — video will be stuck in PROCESSING on Spot interruption

### 🟡 Medium
- [PERF] Line 134: Sequential chunk uploads — use p-queue for concurrent S3 uploads

### 🟢 Low
- [STYLE] Mongoose schema missing `timestamps: true`

### ✅ Looks Good
- Presigned URL expiry correctly set to 900s
- Socket.IO emitting to specific room, not broadcast
```
