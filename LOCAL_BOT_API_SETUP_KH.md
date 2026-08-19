# របៀបដំឡើង Telegram Local Bot API នៅលើ VPS

## អ្វីដែលត្រូវមាន

1. VPS ដែលអាចដំណើរការ Docker និង Docker Compose v2។
2. `TELEGRAM_BOT_TOKEN` ពី `@BotFather`។
3. `TELEGRAM_API_ID` និង `TELEGRAM_API_HASH` ពី `https://my.telegram.org/apps`។
4. Disk ទំនេរធំជាងវីដេអូដែលអ្នកចង់ផ្ញើ។ សម្រាប់ការងារ 2 ក្នុងពេលតែមួយ គួរមានទំហំទំនេរយ៉ាងហោចណាស់ 5 GB។

## ជំហានដំឡើង

បញ្ឈប់ Bot ចាស់សិន ដើម្បីកុំឱ្យ Bot ដូចគ្នារត់ពីរកន្លែង។ Upload project នេះទៅ VPS រួចរត់៖

```bash
cp .env.example .env
nano .env
```

បញ្ចូលតម្លៃពិតតែ 3 នេះ៖

```env
TELEGRAM_BOT_TOKEN=ដាក់_BOT_TOKEN
TELEGRAM_API_ID=ដាក់_API_ID_ជាលេខ
TELEGRAM_API_HASH=ដាក់_API_HASH
```

កុំប្ដូរតម្លៃទាំងនេះ ពេលប្រើ `docker-compose.yml`៖

```env
TELEGRAM_BOT_API_URL=http://telegram-bot-api:8081
BOT_TEMP_DIR=/data/bot-temp
```

បន្ទាប់មករត់៖

```bash
chmod +x deploy_local_api.sh telegram-api-entrypoint.sh
./deploy_local_api.sh
```

ពិនិត្យថា service ទាំងពីរដំណើរការ៖

```bash
docker compose ps
docker compose logs -f --tail=100
```

បន្ទាប់ពីឃើញ Bot started អ្នកអាចផ្ញើ TikTok link ទៅ Bot។ វីដេអូ 115 MB អាចផ្ញើបាន ហើយ file នៅក្នុង hosting នឹងត្រូវលុបដោយស្វ័យប្រវត្តិបន្ទាប់ពីការផ្ញើចប់ ឬបរាជ័យ។

## សុវត្ថិភាព Storage

- Video temp ត្រូវបានលុបគ្រប់ករណីតាម `finally`។
- បើ VPS restart ខណៈកំពុងផ្ញើ Bot នឹងសម្អាត temp ចាស់ពេលចាប់ផ្ដើមឡើងវិញ។
- Local API upload temp ត្រូវបានសម្អាតមុន server start។
- Docker log ត្រូវបានកំណត់ត្រឹម 3 files × 10 MB ក្នុងមួយ service។
- កុំលុប volume `telegram-api-data` ដោយដៃ ព្រោះវាមាន session/database របស់ Local Bot API។
