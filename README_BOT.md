# Theziess Method Telegram Bot — Auto Compression + Local API Option

Bot commands are unchanged:

```text
/check <link>     ពិនិត្យ FPS, Resolution, Bitrate, Codec និង Method
/download <link>  Download ហើយផ្ញើវីដេអូក្នុង Telegram
```

Sending one TikTok link without a command still behaves like `/download`.

## What this fixed version adds

- Reviactyl-compatible automatic compression for videos larger than 49 MB.
- Two-pass H.264 encoding targets about 47 MB for reliable cloud Bot API uploads.
- Source FPS is preserved; the bot never forces 30 FPS or drops to a configured FPS cap.
- 4K sources are resized to a maximum 1080p display size to keep visual quality high at 47 MB.
- Telegram Local Bot API Server for uploads up to 2000 MB.
- The Local Bot API port stays private inside Docker; it is not exposed publicly.
- The bot and Local API share the downloaded file by local path, avoiding a second large upload copy.
- The downloaded video is deleted after send success, Telegram rejection, timeout, cancellation, or another error.
- Bot temp directories left by a hard restart are removed when the bot starts again.
- Local API HTTP temp files left by a crash are removed before Local API starts.
- Docker logs rotate automatically so logs do not fill the VPS disk.

## Reviactyl `.env` (recommended for current hosting)

Copy the example and edit it:

```bash
cp .env.example .env
nano .env
```

Set the new Bot Token and keep Local API URL empty:

```env
TELEGRAM_BOT_TOKEN=BOT_TOKEN_FROM_BOTFATHER
TELEGRAM_BOT_API_URL=
AUTO_COMPRESS_FOR_CLOUD=true
CLOUD_UPLOAD_TARGET_MB=47
COMPRESSION_MAX_SHORT_SIDE=1080
COMPRESSION_PRESET=medium
COMPRESSION_TIMEOUT_SECONDS=1800
```

Start the bot normally from the Reviactyl panel. A 115 MB source is downloaded, compressed near 47 MB while preserving its original FPS, sent to Telegram, and then both the original and compressed temporary files are deleted.

## Optional Local Bot API on a real VPS

Only when using `docker-compose.yml`, also set:

```env
TELEGRAM_API_ID=NUMERIC_API_ID_FROM_MY_TELEGRAM_ORG
TELEGRAM_API_HASH=API_HASH_FROM_MY_TELEGRAM_ORG
```

Docker Compose supplies `TELEGRAM_BOT_API_URL=http://telegram-bot-api:8081` and `BOT_TEMP_DIR=/data/bot-temp` to the bot container automatically.

Never send `.env`, Bot Token, or `api_hash` to another person.

## First Local API deployment on an Ubuntu VPS

Stop the old copy of the bot first. Then, inside this project folder:

```bash
chmod +x deploy_local_api.sh telegram-api-entrypoint.sh
./deploy_local_api.sh
```

The deployment script performs the required one-time `logOut` from Telegram's cloud Bot API, pulls Local Bot API, and starts both services. It creates `.local_bot_api_enabled` so later deployments do not call `logOut` again.

Check status and logs:

```bash
docker compose ps
docker compose logs -f --tail=100
```

Restart later:

```bash
docker compose restart
```

Update/rebuild later:

```bash
docker compose up -d --build
```

## Storage cleanup behavior

The downloaded video exists only in the dedicated `bot-temp` volume while the job is running. The bot deletes its job directory in a `finally` block immediately after the send attempt finishes. On a hard reboot, startup cleanup removes only bot-owned folders named `telegram_tiktok_download_*` or `tiktok_checker_*`; unrelated folders are never deleted.

Do not manually delete `telegram-api-data`. It contains Local Bot API session/database state. The separate `telegram-api-temp` volume is safe for the provided startup script to clean before the API process launches.
