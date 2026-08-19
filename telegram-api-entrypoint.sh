#!/usr/bin/env sh
set -eu

# Only the HTTP upload temp directory is cleared here. Telegram Bot API's
# working directory contains session/database files and must not be deleted.
api_temp_dir="${TELEGRAM_TEMP_DIR:-/tmp/telegram-bot-api}"
mkdir -p "$api_temp_dir"
find "$api_temp_dir" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} \;
chown 101:101 "$api_temp_dir"

exec /docker-entrypoint.sh
