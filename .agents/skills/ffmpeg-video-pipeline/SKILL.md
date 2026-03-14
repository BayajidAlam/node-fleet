---
name: ffmpeg-video-pipeline
version: 1.0.0
description: Best practices for working with FFmpeg video transcoding, DASH/HLS packaging, ECS Fargate processing tasks, and Lambda orchestration in VisionSync. Use when working on container/, lambda/, or video processing logic.
---

> 📋 **Always read [`.agents/CONTEXT.md`](./../CONTEXT.md) first** — it contains the full project architecture, env vars, API routes, and infrastructure details for VisionSync.

# FFmpeg Video Pipeline Skill

## When to Use This Skill

Invoke this skill when:
- Writing or debugging FFmpeg transcoding code in `container/src/ffmpeg-service.ts`
- Working on the ECS container orchestration in `container/src/process-video.ts`
- Modifying the Lambda ECS launcher in `lambda/index.js`
- Debugging video processing failures (wrong manifest, missing chunks, broken DASH playback)
- Adding new output formats (HLS), resolutions, or encoding presets
- Troubleshooting S3 upload/download issues in the pipeline

---

## Practices

### 1. Always Use Proper DASH Output Structure

**Why**: DASH requires a specific file naming pattern and manifest structure. Incorrect `outputOptions` will produce invalid `.mpd` files or cause chunk mismatches during adaptive playback in DASH.js.

**Detection signals**:
- DASH.js throws `MEDIA_ERR_SRC_NOT_SUPPORTED` in the browser
- Manifest file exists but chunks are missing from S3
- Adaptive bitrate switching doesn't occur during playback

**Wrong**:
```typescript
// Incorrect: no seg_duration, no template/timeline flags
ffmpeg()
  .input(inputPath)
  .output(outputPath)
  .format('dash')
  .run();
```

**Right**:
```typescript
// Each resolution gets its own manifest, merged into a master MPD
const outputOptions = [
  '-f dash',
  '-seg_duration 6',             // 6-second segments for good ABR
  '-use_timeline 1',
  '-use_template 1',
  '-init_seg_name', `${tag}_init.m4s`,
  '-media_seg_name', `${tag}_chunk_$Number$.m4s`,
  '-adaptation_sets', 'id=0,streams=v id=1,streams=a',
];
```

**Reference**: FFmpeg DASH muxer docs: https://ffmpeg.org/ffmpeg-formats.html#dash-1

---

### 2. Match FFmpeg Preset to Instance Type

**Why**: VisionSync uses a 70% Spot / 30% Regular Fargate strategy. Spot instances can be interrupted — use faster presets to minimize processing time window. Regular Fargate gets higher quality presets.

**Detection signals**:
- Spot tasks frequently timing out or getting interrupted mid-encode
- Processing times are similar regardless of instance type

**Current logic in `lambda/index.js`**:
```javascript
const useSpot = fileSize < 1_000_000_000 && Math.random() < 0.7;
const ffmpegPreset = useSpot ? 'medium' : 'fast'; // Note: faster = lower quality
```

**Preset tradeoffs**:
| Preset | Speed | Quality | CPU Usage |
|--------|-------|---------|-----------|
| `ultrafast` | Fastest | Lowest | Lowest |
| `fast` | Fast | Good | Medium |
| `medium` | Balanced | Better | Higher |
| `slow` | Slow | Best | Highest |

**Right**:
```typescript
// Use environment variable injected by Lambda
const preset = process.env.FFMPEG_PRESET || 'medium';

ffmpeg()
  .input(inputPath)
  .videoCodec('libx264')
  .addOptions([`-preset ${preset}`, `-crf 23`])
  .audioCodec('aac')
  .audioBitrate('128k');
```

---

### 3. Handle SIGTERM Gracefully for Spot Interruptions

**Why**: AWS Fargate Spot instances receive a 2-minute warning before interruption via SIGTERM. Without a handler, the task dies mid-encode, leaving partial uploads in S3 and the video stuck at `PROCESSING` status.

**Detection signals**:
- Videos stuck in `PROCESSING` state in MongoDB
- Partial `.m4s` chunk files in S3 processed bucket
- No `webhook` call received by backend after task termination

**Right**:
```typescript
// At the top of process-video.ts
let isShuttingDown = false;

process.on('SIGTERM', async () => {
  console.log('[SHUTDOWN] Received SIGTERM - Spot interruption incoming');
  isShuttingDown = true;

  // Notify backend of failure so UI shows correct state
  await webhookService.notify({
    videoId: process.env.VIDEO_ID!,
    status: 'failed',
    error: 'Spot instance interrupted'
  });

  process.exit(0);
});

// Check flag in long-running loops
for (const resolution of resolutions) {
  if (isShuttingDown) break;
  await processResolution(resolution);
}
```

---

### 4. Stream Large Files from S3 Instead of Full Download

**Why**: Downloading videos to ECS disk before processing wastes time and disk space. Files >2GB may exceed ECS task ephemeral storage (20GB default). Streaming directly into FFmpeg stdin is faster and memory efficient.

**Detection signals**:
- ECS tasks failing with `No space left on device`
- Long initialization times before FFmpeg starts
- Large video files (>2GB) failing consistently

**Right**:
```typescript
import { GetObjectCommand } from '@aws-sdk/client-s3';
import { spawn } from 'child_process';

async function streamVideoFromS3(bucket: string, key: string): Promise<NodeJS.ReadableStream> {
  const command = new GetObjectCommand({ Bucket: bucket, Key: key });
  const response = await s3Client.send(command);
  return response.Body as NodeJS.ReadableStream;
}

// Pipe S3 stream directly into FFmpeg
const s3Stream = await streamVideoFromS3(bucket, s3Key);
const ffmpegProcess = spawn('ffmpeg', ['-i', 'pipe:0', ...outputArgs]);
s3Stream.pipe(ffmpegProcess.stdin!);
```

---

### 5. Upload Chunks Concurrently with Multipart Upload

**Why**: Uploading processed chunks sequentially is slow. FFmpeg generates many small `.m4s` files. Using multipart upload and `Promise.all` with a concurrency limit dramatically reduces post-processing time.

**Detection signals**:
- Processing finishes quickly but S3 upload takes much longer
- Webhook arrives long after FFmpeg exits

**Right**:
```typescript
import PQueue from 'p-queue'; // npm install p-queue

const uploadQueue = new PQueue({ concurrency: 5 }); // 5 parallel uploads

const uploadTasks = chunkFiles.map(file =>
  uploadQueue.add(() => uploadToS3(file, processedBucket, `${videoId}/${file.name}`))
);

await Promise.all(uploadTasks);
```

---

### 6. Validate the MPD Manifest Before Sending Webhook

**Why**: A corrupt or incomplete `manifest.mpd` causes client-side playback failure even when all chunks exist. Validate the manifest before notifying the backend.

**Detection signals**:
- Backend marks video as `ready` but DASH.js fails to play
- `manifest.mpd` references resolutions that weren't generated

**Right**:
```typescript
import { readFileSync } from 'fs';

function validateManifest(manifestPath: string, expectedResolutions: string[]): boolean {
  const content = readFileSync(manifestPath, 'utf-8');

  // Check all expected Representation IDs are present
  for (const res of expectedResolutions) {
    if (!content.includes(`id="${res}"`)) {
      console.error(`[MANIFEST] Missing representation: ${res}`);
      return false;
    }
  }

  // Check manifest has both video and audio adaptation sets
  const hasVideo = content.includes('contentType="video"');
  const hasAudio = content.includes('contentType="audio"');

  return hasVideo && hasAudio;
}
```

---

## Quick Reference

| Issue | Likely Cause | Fix |
|-------|-------------|-----|
| DASH.js won't play | Bad `manifest.mpd` | Validate manifest, check segment naming |
| Video stuck in PROCESSING | SIGTERM not handled | Add SIGTERM handler + webhook failure call |
| ECS task out of disk | Full file download | Stream from S3 via pipe |
| Slow uploads after encoding | Sequential S3 uploads | Use `p-queue` for concurrent uploads |
| Adaptive quality not switching | Missing audio adaptation set | Add `-adaptation_sets "id=0,streams=v id=1,streams=a"` |
| Spot tasks interrupted | Long encode time | Use `fast` preset on Spot, process fewer resolutions |

## VisionSync Specific Context

- **ECS Task config**: 2 vCPU, 4GB RAM, ephemeral storage: 20GB
- **S3 Raw bucket**: source video landing zone (triggered by S3 → SQS)  
- **S3 Processed bucket**: output destination for chunks + manifest
- **Resolutions**: Spot → `['720p','480p','360p']`, Regular → `['1080p','720p','480p','360p']`
- **Segment duration**: 6 seconds (good balance of seek performance vs chunk count)
- **Webhook endpoint**: `POST <WEBHOOK_URL>/api/webhook/processing-complete`
- **CloudFront serves** the processed bucket — all manifest/chunk URLs use the CloudFront domain
