import shutil
import subprocess

for cmd in ["ffmpeg", "ffprobe", "yt-dlp"]:
    path = shutil.which(cmd)

    if path:
        print(f"✅ {cmd}: {path}")

        result = subprocess.run(
            [cmd, "-version"],
            capture_output=True,
            text=True
        )

        print(result.stdout.splitlines()[0])
    else:
        print(f"❌ {cmd}: NOT FOUND")