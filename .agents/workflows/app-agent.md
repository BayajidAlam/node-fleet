---
description: Application agent for VisionSync — works on backend API, video processing container, Lambda orchestrator, and frontend
---

# VisionSync — App Agent

> 📋 **Read [`.agents/CONTEXT.md`](./../CONTEXT.md) first** before any work. It contains the full API endpoints, video processing specs, status flow, env vars, and Socket.IO events.

## Scope
This agent handles all application code:
- `server/` — Node.js/Express/TypeScript backend API + Socket.IO
- `container/` — FFmpeg video processing worker (ECS)
- `lambda/` — ECS task orchestrator (SQS trigger)
- `client/` — React/TypeScript frontend (DASH.js player)

**Does NOT touch:** `IaC/`, `ansible/`, `Makefile` → use `/infra-agent` for those.

## Skills to Load
Before working, read the relevant skill:

- **[`nodejs-backend`](./../skills/nodejs-backend/SKILL.md)** — working on `server/`
- **[`ffmpeg-video-pipeline`](./../skills/ffmpeg-video-pipeline/SKILL.md)** — working on `container/` or `lambda/`

## Key Rules for This Agent

### Backend (`server/`)
- Use AWS SDK **v3** only — `new S3Client()` + `client.send(new XCommand(...))`
- Socket.IO events emit to specific rooms (`io.to(videoId).emit(...)`) — never broadcast
- `app.set('trust proxy', 1)` must be set — backend sits behind ALB
- MongoDB connection must use the full replica set URI with `?replicaSet=rs0`
- Rate limiting: general API = 100 req/15min, upload = 5 req/15min (configurable via env)
- All route handlers typed: `Request<{}, {}, BodyType>` — no implicit `any`

### Video Status Flow
```
UPLOADING → UPLOADED → PROCESSING → READY
                                  ↘ ERROR
```
- `POST /api/upload/generate-presigned-url` → creates MongoDB record → status: `UPLOADING`
- `POST /api/upload/confirm/:id` → updates status: `UPLOADED` (UI feedback only)
- Lambda picks up SQS (triggered by S3 Event Notification) → status: `PROCESSING`
- ECS webhook → `POST /api/webhook/processing-complete` → status: `READY` or `ERROR`

### Container (`container/`)
- FFmpeg output must use `-f dash -seg_duration 6 -use_timeline 1 -use_template 1`
- Resolutions: Regular Fargate → 1080p/720p/480p/360p | Spot → 720p/480p/360p
- **SIGTERM handler is mandatory** — Spot instances get 2-min warning before interruption
  - Handler must: set shutdown flag + POST failure webhook to backend before `process.exit(0)`
- Validate `manifest.mpd` before sending completion webhook
- S3 chunk uploads should be concurrent (use `p-queue` with concurrency 5)

### Lambda (`lambda/`)
- Spot selection: `fileSize < 1GB && random() < ECS_SPOT_PERCENTAGE/100`
- Pass `FFMPEG_PRESET` env var to ECS task: `medium` for Spot, `fast` for Regular
- Delete SQS message **only after** ECS task is launched successfully
- All required ECS env vars: `VIDEO_ID`, `S3_KEY`, `WEBHOOK_URL`, `FFMPEG_PRESET`

## API Reference (Quick)
```
POST /api/upload/generate-presigned-url  → { presignedUrl, videoId }
POST /api/upload/confirm/:id             → updates status to UPLOADED
GET  /api/videos/                        → list all videos (Redis cached)
GET  /api/videos/:id                     → single video (Redis cached)
GET  /api/videos/:id/status              → video status
GET  /api/videos/search?q=               → search (Redis cached)
POST /api/webhook/processing-complete    → ECS completion callback
GET  /health                             → ALB health check (must return 200)
```

## Socket.IO Events
```
Server → Client:  video:status  { videoId, status, manifestUrl?, error? }
Client → Server:  join:video    { videoId }
```

## Local Development
```bash
# Terminal 1 — Backend
cd server && npm run dev

# Terminal 2 — Frontend
cd client && npm run dev

# Terminal 3 — MongoDB (local)
mongod --replSet rs0

# Terminal 4 — Redis (local)
redis-server
```
