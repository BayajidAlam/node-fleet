---
name: nodejs-backend
version: 1.0.0
description: Best practices for working on the VisionSync Node.js/Express/TypeScript backend. Covers Socket.IO patterns, rate limiting algorithms, AWS SDK v3 integration, Mongoose schemas, S3 presigned URLs, SQS messaging, and TypeScript patterns.
---

> 📋 **Always read [`.agents/CONTEXT.md`](./../CONTEXT.md) first** — it contains the full project architecture, env vars, API routes, and infrastructure details for VisionSync.

# Node.js Backend Skill

## When to Use This Skill

Invoke this skill when:
- Adding new API routes in `server/src/routes/`
- Working on Socket.IO real-time events in `server/src/socket/`
- Modifying rate limiting logic in `server/src/middleware/`
- Integrating with AWS services (S3, SQS, CloudFront) via SDK
- Writing or debugging Mongoose schemas in `server/src/model/`
- Fixing TypeScript type errors in Express handlers

---

## Project Structure

```
server/src/
├── app.ts              # Express app factory
├── server.ts           # Entry point (HTTP + Socket.IO init)
├── config/
│   ├── db.ts          # MongoDB connection (replica set aware)
│   ├── redis.ts       # Redis connection
│   └── aws.ts         # AWS SDK clients
├── model/             # Mongoose schemas
├── routes/            # Express route handlers
│   ├── video.routes.ts
│   ├── webhook.routes.ts
│   └── health.routes.ts
├── services/          # Business logic
│   ├── video.service.ts
│   ├── s3.service.ts
│   └── sqs.service.ts
├── middleware/        # Rate limiting, auth, validation
├── socket/            # Socket.IO event handlers
└── types/             # Shared TypeScript interfaces
```

---

## Practices

### 1. Use AWS SDK v3 Client Pattern (Not v2)

**Why**: VisionSync uses AWS SDK v3 which uses a command/client pattern instead of method chaining. v2-style code (`new AWS.S3()`) won't work with the installed package.

**Detection signals**:
- `Cannot find module 'aws-sdk'` errors
- `s3.getObject().promise()` — this is v2 syntax
- TypeScript errors like `Property 'upload' does not exist on type 'S3Client'`

**Wrong** (SDK v2):
```typescript
import AWS from 'aws-sdk';
const s3 = new AWS.S3();
const result = await s3.getObject({ Bucket, Key }).promise();
```

**Right** (SDK v3):
```typescript
import { S3Client, GetObjectCommand, PutObjectCommand } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';

const s3Client = new S3Client({ region: process.env.AWS_REGION });

// Get object
const command = new GetObjectCommand({ Bucket: bucket, Key: key });
const response = await s3Client.send(command);

// Generate presigned URL (15 minute expiry)
const presignedUrl = await getSignedUrl(s3Client, command, { expiresIn: 900 });
```

---

### 2. Emit Socket.IO Events from the Webhook Handler

**Why**: Video processing status updates flow from the ECS container via webhook → backend → Socket.IO → frontend. The `io` instance must be accessible from route handlers without circular imports.

**Detection signals**:
- Frontend doesn't receive `video:ready` events after processing completes
- `io is not defined` errors in webhook route
- Socket.IO events emitted to wrong room

**Right** — attach `io` to the Express app:
```typescript
// server.ts
const io = new Server(httpServer, { cors: { origin: process.env.SOCKET_IO_CORS_ORIGIN } });
app.set('io', io); // Make io available to route handlers

// webhook.routes.ts
router.post('/processing-complete', async (req, res) => {
  const { videoId, status, manifestUrl, thumbnailUrl } = req.body;
  const io = req.app.get('io') as Server;

  // Update MongoDB
  await Video.findByIdAndUpdate(videoId, { status, manifestUrl, thumbnailUrl });

  // Emit to the specific video room
  io.to(videoId).emit('video:ready', { videoId, manifestUrl, thumbnailUrl });

  res.json({ success: true });
});

// Client joins the room when upload starts:
// socket.join(videoId)
```

---

### 3. Connect to MongoDB Replica Set (Not Single Node)

**Why**: Production uses a 3-node replica set (`rs0`). Connecting without `replicaSet` option causes write failures when the primary changes, and read preference won't work for secondaries.

**Detection signals**:
- `MongoServerError: not primary` errors in logs
- Reads not distributed across secondary nodes
- Connection drops when MongoDB primary steps down

**Wrong**:
```typescript
// Single node connection - breaks with replica set
mongoose.connect('mongodb://10.10.4.10:27017/vision-sync');
```

**Right**:
```typescript
const MONGODB_URI = process.env.MONGODB_URI ||
  'mongodb://10.10.4.10:27017,10.10.4.11:27017,10.10.4.12:27017/vision-sync?replicaSet=rs0';

await mongoose.connect(MONGODB_URI, {
  readPreference: 'secondaryPreferred', // Reads from secondary, writes go to primary
  serverSelectionTimeoutMS: 5000,
  heartbeatFrequencyMS: 2000,
});
```

---

### 4. Type Express Request Handlers Correctly

**Why**: VisionSync uses TypeScript. Untyped handlers cause runtime errors and the `hashPassword` and similar errors seen in past conversations. Always type both request params and response body.

**Detection signals**:
- TypeScript error `Property 'X' does not exist on type 'Document<...>'`
- `This expression is not callable. Type 'unknown' has no call signatures`
- `No overload matches this call` on `Model.create()`

**Right**:
```typescript
import { Request, Response, NextFunction } from 'express';

// Type route params and body
interface UploadUrlRequest {
  filename: string;
  fileSize: number;
  contentType: string;
}

router.post('/upload-url', async (
  req: Request<{}, {}, UploadUrlRequest>,
  res: Response,
  next: NextFunction
) => {
  const { filename, fileSize, contentType } = req.body;
  // ...
});

// For Mongoose instance methods - define them in the interface
interface IVideo {
  videoId: string;
  status: 'PENDING' | 'PROCESSING' | 'READY' | 'FAILED';
  manifestUrl?: string;
}

// For model methods, declare in schema
videoSchema.methods.markReady = function(manifestUrl: string) {
  this.status = 'READY';
  this.manifestUrl = manifestUrl;
  return this.save();
};
```

---

### 5. Implement Rate Limiting Correctly for Upload Endpoints

**Why**: Upload endpoints are expensive (trigger S3 + SQS + ECS). They need stricter rate limits than general API endpoints. VisionSync uses multiple algorithms for different route types.

**Detection signals**:
- Users can spam video uploads exhausting ECS capacity
- Rate limiter applies the same limit to all routes
- `req.ip` is undefined behind ALB (trust proxy not set)

**Right**:
```typescript
import rateLimit from 'express-rate-limit';
import RedisStore from 'rate-limit-redis';

// In app.ts — trust ALB proxy
app.set('trust proxy', 1);

// Strict limit for video upload (triggers ECS task)
const uploadLimiter = rateLimit({
  windowMs: 60 * 60 * 1000, // 1 hour
  max: 10,                    // 10 uploads per hour per IP
  standardHeaders: true,
  store: new RedisStore({ client: redisClient }),
  message: { error: 'Too many upload requests, please try again later.' }
});

// General API limit
const apiLimiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 100,
  store: new RedisStore({ client: redisClient }),
});

app.use('/api/', apiLimiter);
app.use('/api/videos/upload-url', uploadLimiter);
```

---

### 6. Send SQS Messages with the Correct Message Structure

**Why**: The Lambda function reads specific fields from the SQS message body to determine ECS task configuration. Sending malformed messages causes Lambda to fail silently (messages go to DLQ).

**Detection signals**:
- Video stays in `PROCESSING` after upload
- SQS messages piling up in dead letter queue
- Lambda logs show JSON parse errors

**Right**:
```typescript
import { SQSClient, SendMessageCommand } from '@aws-sdk/client-sqs';

const sqsClient = new SQSClient({ region: process.env.AWS_REGION });

async function sendVideoProcessingMessage(
  bucket: string,
  s3Key: string,
  videoId: string,
  fileSize: number,
  webhookUrl: string
): Promise<void> {
  const message = {
    videoId,
    bucket,
    s3Key,
    fileSize,       // Lambda uses this for Spot/Regular selection
    webhookUrl,     // ECS container uses this to POST completion
  };

  await sqsClient.send(new SendMessageCommand({
    QueueUrl: process.env.SQS_QUEUE_URL!,
    MessageBody: JSON.stringify(message),
    MessageGroupId: videoId, // If using FIFO queue
  }));
}
```

---

## Quick Reference

| Issue | Likely Cause | Fix |
|-------|-------------|-----|
| AWS SDK method not found | Using v2 syntax with v3 | Use `client.send(new XCommand(...))` |
| Socket.IO event not received | `io` not accessible in route | `app.set('io', io)` + `req.app.get('io')` |
| `req.ip` is ALB IP | Trust proxy not set | `app.set('trust proxy', 1)` |
| MongoDB write fails after failover | No `replicaSet` in URI | Add `?replicaSet=rs0` and all member IPs |
| TypeScript method not found | Method not in interface | Declare in `IVideo` interface + schema |
| SQS message goes to DLQ | Missing required field in body | Include `videoId`, `bucket`, `s3Key`, `fileSize`, `webhookUrl` |

## VisionSync Specific Context

- **Server port**: `5000`
- **Socket.IO rooms**: each video has its own room named by `videoId`
- **MongoDB**: replica set `rs0`, 3 nodes — always use replica set URI
- **Redis**: used for rate limiting store AND Socket.IO adapter
- **S3 presigned URL expiry**: 15 minutes (900 seconds)
- **Webhook route**: `POST /api/webhook/processing-complete`
- **Health route**: `GET /health` — used by ALB health checks, must return 200
