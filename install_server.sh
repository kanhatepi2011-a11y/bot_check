#!/usr/bin/env bash
set -euo pipefail

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3 python3-pip python3-venv ffmpeg unzip
elif command -v pkg >/dev/null 2>&1; then
  pkg update -y
  pkg install -y python ffmpeg
else
  echo "Unsupported package manager. Install Python 3, pip, ffmpeg and ffprobe manually."
  exit 1
fi

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
chmod +x telegram_bot.py tiktok_checker.py start_bot.sh

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. Add TELEGRAM_BOT_TOKEN before starting."
fi

echo "Installation complete."
echo "1) nano .env"
echo "2) ./start_bot.sh"
