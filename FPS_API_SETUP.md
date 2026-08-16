# Theziess Method FPS API on PEACHY

The Telegram bot and FPS HTTP API run in the same Python process.

## Network

Use PEACHY allocation port `3008` for the FPS API.

The API listens on:

- `0.0.0.0:3008`
- `GET /health`
- `POST /api/check-video/start`
- `GET /api/check-video/status?job_id=...`
- `POST /api/check-video` (synchronous manual-test endpoint)

## Environment

Set:

```env
FPS_API_HOST=0.0.0.0
FPS_API_PORT=3008
FPS_API_KEY=<same secret used by Vercel>
```

## Test after restart

```bash
curl http://panel.peachygang.app:3008/health
```

Expected:

```json
{"ok":true,"service":"theziess-fps-api","status":"ready","port":3008}
```

Start a job:

```bash
curl -X POST http://panel.peachygang.app:3008/api/check-video/start \\
  -H 'Content-Type: application/json' \\
  -H 'Authorization: Bearer YOUR_SECRET' \\
  -d '{"url":"https://vt.tiktok.com/..."}'
```

The returned `jobId` can be polled at `/api/check-video/status?job_id=...`.
