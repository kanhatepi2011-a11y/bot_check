# TikTok Quality Checker Telegram Bot

Bot នេះមាន command តែ 2៖

```text
/check <link>     ពិនិត្យ FPS, Resolution, Bitrate និង Codec
/download <link>  Download ហើយផ្ញើវីដេអូក្នុង Telegram
```

ផ្ញើ link តែមួយដោយមិនដាក់ command ក៏ស្មើនឹង `/download` ដែរ៖

```text
https://vt.tiktok.com/...
```

## Setup

```bash
chmod +x install_server.sh start_bot.sh
./install_server.sh
nano .env
./start_bot.sh
```

`.env` example:

```env
TELEGRAM_BOT_TOKEN=YOUR_BOTFATHER_TOKEN
MAX_CONCURRENT_JOBS=2
JOB_TIMEOUT_SECONDS=600
ALLOWED_USER_IDS=
```

វីដេអូត្រូវបានរក្សាទុកក្នុង temporary directory ហើយលុបដោយស្វ័យប្រវត្តិក្រោយ analyze ឬផ្ញើទៅ Telegram ចប់។
