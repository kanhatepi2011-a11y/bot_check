#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

echo "== TikTok Quality Checker v1.1 Installer =="

pkg update -y
pkg install -y python ffmpeg

echo "[1/2] Installing latest yt-dlp pre-release..."
python -m pip install -U --pre "yt-dlp[default]"

echo "[2/2] Trying optional browser impersonation support..."
if python -m pip install -U curl_cffi; then
  echo "curl_cffi installed."
else
  echo "curl_cffi is unavailable on this Termux device; continuing without it."
fi

if [ -f "tiktok_checker.py" ]; then
  chmod +x tiktok_checker.py
fi

echo
echo "Installation complete."
echo "Run:"
echo "  python tiktok_checker.py --help"
echo
echo "Optional Android shared storage access:"
echo "  termux-setup-storage"
echo
echo "For difficult TikTok videos, put cookies.txt in this folder"
echo "or /sdcard/Download/cookies.txt."
