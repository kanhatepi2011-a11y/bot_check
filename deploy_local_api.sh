#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker is not installed."
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: Docker Compose v2 is not installed."
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
  echo "Fill TELEGRAM_BOT_TOKEN, TELEGRAM_API_ID and TELEGRAM_API_HASH, then run this script again."
  exit 1
fi

for required_key in TELEGRAM_BOT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH; do
  if ! grep -Eq "^${required_key}=.+" .env; then
    echo "ERROR: ${required_key} is missing in .env"
    exit 1
  fi

  required_value="$(sed -n "s/^${required_key}=//p" .env | tail -n 1)"
  case "$required_value" in
    YOUR_*|CHANGE_ME*|"")
      echo "ERROR: Replace the placeholder value for ${required_key} in .env"
      exit 1
      ;;
  esac
done

docker compose build bot

if [ ! -f .local_bot_api_enabled ]; then
  echo "Switching the bot from Telegram cloud API to Local Bot API..."
  docker compose run --rm --no-deps bot python switch_to_local_api.py
  touch .local_bot_api_enabled
fi

docker compose pull telegram-bot-api
docker compose up -d
docker compose ps

echo "Local Bot API and the Telegram bot are running."
echo "View logs: docker compose logs -f --tail=100"
