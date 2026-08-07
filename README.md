# TikTok Video Quality Checker — Termux

## Install

```bash
pkg update -y && pkg install -y python ffmpeg && python -m pip install -U yt-dlp
```

Optional access to `/sdcard`:

```bash
termux-setup-storage
```

## Run

```bash
python tiktok_checker.py "https://www.tiktok.com/@user/video/123"
python tiktok_checker.py "/sdcard/Download/video.mp4"
python tiktok_checker.py --batch urls.txt --csv results.csv
python tiktok_checker.py --compare video1.mp4 video2.mp4
```

Batch file example:

```text
https://www.tiktok.com/@user/video/123
https://www.tiktok.com/@user/video/456
/storage/emulated/0/Download/local.mp4
```
