# Reviactyl: Auto Compress ក្រោម 49 MB

Version នេះមិនត្រូវការ Telegram Local Bot API Server នៅលើ Reviactyl ទេ។ បើវីដេអូលើស 49 MB Bot នឹងបង្ហាប់វាដោយស្វ័យប្រវត្តិទៅប្រហែល 47 MB រួចផ្ញើតាម Telegram Bot API ផ្លូវការ។

## Environment Variables

```env
TELEGRAM_BOT_TOKEN=ដាក់_TOKEN_ថ្មី
TELEGRAM_BOT_API_URL=
AUTO_COMPRESS_FOR_CLOUD=true
CLOUD_UPLOAD_TARGET_MB=47
COMPRESSION_MAX_SHORT_SIDE=1080
COMPRESSION_PRESET=medium
COMPRESSION_TIMEOUT_SECONDS=1800
MAX_CONCURRENT_JOBS=1
MAX_CONCURRENT_COMPRESSIONS=1
```

នៅក្នុង Reviactyl Startup Variables ត្រូវលុប `TELEGRAM_BOT_API_URL` ចេញ ឬទុក value ទទេ។ កុំដាក់ `http://telegram-bot-api:8081`។

## របៀបដំណើរការ

Upload files ថ្មីទាំងអស់ទៅ hosting ហើយកំណត់ `PY_FILE=telegram_bot.py` ដូចដើម។ Stop server រង់ចាំឱ្យ offline រួច Start វិញ។

ពេលផ្ញើវីដេអូ 115 MB Bot នឹងធ្វើ៖

1. Download file ដើម។
2. បង្ហាប់ជា H.264 MP4 ដោយ two-pass target-size encoding។
3. រក្សា FPS ដើម។ ឧទាហរណ៍ 60 FPS នៅតែ 60 FPS។
4. បន្ថយ 4K ទៅ 1080p ដើម្បីរក្សារូបភាពឱ្យច្បាស់នៅក្រោម 49 MB។
5. ផ្ញើ file ប្រហែល 47 MB ទៅ Telegram។
6. លុប original, compressed output និង FFmpeg pass files ចេញពី hosting ទោះផ្ញើជោគជ័យ ឬបរាជ័យក៏ដោយ។

`ffmpeg` និង `ffprobe` ត្រូវតែមាននៅក្នុង hosting។ Log ចាស់របស់អ្នកបង្ហាញថា hosting មាន FFmpeg រួចហើយ។
