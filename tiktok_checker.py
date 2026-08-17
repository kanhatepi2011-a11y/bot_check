#!/usr/bin/env python3
"""
TikTok Video Quality Checker for Termux / Linux.

Features:
- Download one video URL with yt-dlp
- Analyze local video files offline with ffprobe JSON
- FPS, resolution, bitrate, codecs, size, duration
- Batch URL/local-file processing
- CSV export
- Two-video comparison
- Automatic temporary-file cleanup
- Graceful errors and colored terminal output
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse


APP_NAME = "TikTok FPS Quality Checker"
LINE = "=" * 58
TIKTOK_FORMAT_SELECTOR = (
    os.environ.get("TIKTOK_FORMAT_SELECTOR", "").strip()
    or "b[ext=mp4]/b/bv*+ba"
)


def get_temp_root() -> Path:
    """
    Return a disk-backed temp directory for large TikTok videos.

    System /tmp is often a small tmpfs inside hosting containers, so 4K videos
    can fail with Errno 28 even when the server's main disk still has space.
    BOT_TEMP_DIR can override the location. By default we keep temporary files
    beside this bot project, on the same persistent storage allocation.
    """
    configured = os.environ.get("BOT_TEMP_DIR", "").strip()
    root = (
        Path(configured).expanduser()
        if configured
        else Path(__file__).resolve().parent / ".bot_tmp"
    )

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CheckerError(
            f"មិនអាចបង្កើត temp folder បាន: {root} ({exc})"
        ) from exc

    if not root.is_dir():
        raise CheckerError(f"Temp path មិនមែនជា folder: {root}")

    return root


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"

    @classmethod
    def disable(cls) -> None:
        for name in ("RESET", "BOLD", "RED", "GREEN", "YELLOW", "BLUE", "CYAN", "MAGENTA"):
            setattr(cls, name, "")


@dataclass
class VideoReport:
    source: str
    file_name: str
    fps: float
    width: int
    height: int
    resolution_label: str
    video_bitrate_kbps: float
    overall_bitrate_kbps: float
    video_codec: str
    audio_codec: str
    pixel_format: str
    duration_seconds: float
    file_size_mb: float
    quality_score: str
    downloaded: bool
    method_detected: bool = False
    method_name: str = "Not detected"
    error: str = ""

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


class CheckerError(RuntimeError):
    """Expected user-facing error."""


def print_header() -> None:
    print(f"{Color.CYAN}{Color.BOLD}{LINE}")
    print(f"🎬 {APP_NAME}")
    print(f"{LINE}{Color.RESET}")


def info(message: str) -> None:
    print(f"{Color.BLUE}ℹ️  {message}{Color.RESET}")


def success(message: str) -> None:
    print(f"{Color.GREEN}✅ {message}{Color.RESET}")


def warning(message: str) -> None:
    print(f"{Color.YELLOW}⚠️  {message}{Color.RESET}")


def error(message: str) -> None:
    print(f"{Color.RED}❌ {message}{Color.RESET}", file=sys.stderr)


def require_command(command: str, install_hint: str) -> None:
    if shutil.which(command) is None:
        raise CheckerError(
            f"រកមិនឃើញ command '{command}'។\n"
            f"ដំឡើងដោយ៖ {install_hint}"
        )


def is_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False


def run_command(
    command: list[str],
    *,
    capture_output: bool = False,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess[str]:
    try:
        # Force yt-dlp/ffmpeg and other child processes to use the bot's
        # disk-backed temporary directory instead of a possibly tiny /tmp tmpfs.
        child_env = os.environ.copy()
        temp_root = get_temp_root()
        child_env["TMPDIR"] = str(temp_root)
        child_env["TMP"] = str(temp_root)
        child_env["TEMP"] = str(temp_root)

        return subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            timeout=timeout,
            env=child_env,
        )
    except FileNotFoundError as exc:
        raise CheckerError(f"រកមិនឃើញ command: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CheckerError(f"Command ចំណាយពេលយូរពេក: {command[0]}") from exc
    except OSError as exc:
        raise CheckerError(f"មិនអាចដំណើរការ command បាន: {exc}") from exc


def find_cookie_file() -> Optional[Path]:
    """Find a Netscape cookies.txt file from env or common local paths."""
    candidates: list[Path] = []

    env_cookie = os.environ.get("TIKTOK_COOKIES")
    if env_cookie:
        candidates.append(Path(env_cookie).expanduser())

    candidates.extend([
        Path.cwd() / "cookies.txt",
        Path.home() / "cookies.txt",
        Path("/sdcard/Download/cookies.txt"),
        Path("/storage/emulated/0/Download/cookies.txt"),
    ])

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.is_file() and resolved.stat().st_size > 0:
                return resolved
        except OSError:
            continue
    return None


def chrome_impersonation_available() -> bool:
    """Return True only when yt-dlp has an available Chrome impersonation target."""
    result = run_command(
        [sys.executable, "-m", "yt_dlp", "--list-impersonate-targets"],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        return False

    output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    return any(
        "chrome" in line and "unavailable" not in line
        for line in output.splitlines()
    )


def clean_partial_downloads(output_dir: Path) -> None:
    for pattern in ("video.*", "*.part", "*.ytdl", "*.temp"):
        for path in output_dir.glob(pattern):
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass


def download_video(url: str, output_dir: Path) -> Path:
    try:
        import yt_dlp  # noqa: F401
    except ImportError as exc:
        raise CheckerError(
            "រកមិនឃើញ Python package 'yt-dlp'។ "
            "សូមដំឡើងដោយ: python -m pip install -U yt-dlp"
        ) from exc

    require_command("ffmpeg", "apt install ffmpeg -y")

    output_template = str(output_dir / "video.%(ext)s")
    cookie_file = find_cookie_file()
    can_impersonate = chrome_impersonation_available()

    common = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--newline",
        "--no-warnings",
        "--restrict-filenames",
        "--merge-output-format",
        "mp4",
        "--extractor-retries",
        "3",
        "--fragment-retries",
        "3",
        "--retries",
        "3",
        "--socket-timeout",
        "30",
        "--retry-sleep",
        "extractor:1",
        "--retry-sleep",
        "http:1",
        "-f",
        # Prefer TikTok's original single-file MP4. Merging separate streams
        # can create a new container and discard the Method/Artist metadata
        # that this checker is expected to read.
        TIKTOK_FORMAT_SELECTOR,
        "-o",
        output_template,
    ]

    # Each strategy starts from a clean temporary directory.
    strategies: list[tuple[str, list[str]]] = [
        ("Normal TikTok extractor", []),
        (
            "Mobile browser headers",
            [
                "--user-agent",
                (
                    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Mobile Safari/537.36"
                ),
                "--add-header",
                "Referer:https://www.tiktok.com/",
                "--add-header",
                "Accept-Language:en-US,en;q=0.9",
            ],
        ),
    ]

    if can_impersonate:
        strategies.append(("Chrome impersonation", ["--impersonate", "chrome"]))

    if cookie_file:
        cookie_args = ["--cookies", str(cookie_file)]
        strategies.append((f"TikTok cookies ({cookie_file})", cookie_args))
        if can_impersonate:
            strategies.append(
                (
                    f"Cookies + Chrome impersonation ({cookie_file})",
                    cookie_args + ["--impersonate", "chrome"],
                )
            )

    last_detail = ""
    print(f"\n{Color.MAGENTA}📥 កំពុង Download...{Color.RESET}")

    for attempt, (name, extra_args) in enumerate(strategies, start=1):
        clean_partial_downloads(output_dir)
        print(
            f"{Color.BLUE}↻ Strategy {attempt}/{len(strategies)}: "
            f"{name}{Color.RESET}"
        )

        command = common + extra_args + [url]
        result = run_command(command, capture_output=True, timeout=300)

        # Preserve useful progress and extractor output.
        combined_output = "\n".join(
            part.strip()
            for part in (result.stdout or "", result.stderr or "")
            if part.strip()
        )
        if combined_output:
            print(combined_output)

        candidates = sorted(
            (
                path
                for path in output_dir.glob("video.*")
                if path.is_file()
                and not path.name.endswith((".part", ".ytdl", ".temp"))
            ),
            key=lambda path: path.stat().st_size,
            reverse=True,
        )

        if result.returncode == 0 and candidates:
            success(f"Download ជោគជ័យដោយ៖ {name}")
            return candidates[0]

        last_detail = combined_output[-1200:] if combined_output else ""
        warning(f"Strategy '{name}' មិនជោគជ័យ។ កំពុងសាកវិធីបន្ទាប់...")

    # Keep detailed yt-dlp output only in the server console.
    # Telegram users receive a short, clean error message.
    
    if last_detail:
        print(last_detail, file=sys.stderr)
    raise CheckerError("មិនអាចពិនិត្យវីដេអូនេះបាន")



def parse_fraction(value: object) -> float:
    if value is None:
        return 0.0

    text = str(value).strip()
    if not text or text in {"0/0", "N/A", "nan"}:
        return 0.0

    try:
        return float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        try:
            return float(text)
        except ValueError:
            return 0.0


def safe_float(value: object) -> float:
    if value in (None, "", "N/A"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def resolution_label(width: int, height: int) -> str:
    long_side = max(width, height)
    short_side = min(width, height)

    if long_side >= 7680 or short_side >= 4320:
        return "8K UHD"
    if long_side >= 3840 or short_side >= 2160:
        return "4K UHD"
    if long_side >= 2560 or short_side >= 1440:
        return "2K / QHD"
    if long_side >= 1920 or short_side >= 1080:
        return "Full HD"
    if long_side >= 1280 or short_side >= 720:
        return "HD"
    if long_side >= 854 or short_side >= 480:
        return "SD 480p"
    return "Low Resolution"


def quality_score(fps: float, width: int, height: int, bitrate_kbps: float) -> str:
    pixels = width * height

    if fps >= 59 and pixels >= 1920 * 1080 and bitrate_kbps >= 4000:
        return "EXCELLENT"
    if fps >= 50 and pixels >= 1280 * 720 and bitrate_kbps >= 2500:
        return "VERY GOOD"
    if fps >= 29 and pixels >= 1280 * 720 and bitrate_kbps >= 1500:
        return "GOOD"
    if fps >= 24 and pixels >= 854 * 480:
        return "FAIR"
    return "LOW"


def pick_stream(streams: list[dict], codec_type: str) -> dict:
    for stream in streams:
        if stream.get("codec_type") == codec_type:
            return stream
    return {}


PRIMARY_METHOD_TAG_KEYS = (
    "method",
    "artist",
)

SECONDARY_METHOD_TAG_KEYS = (
    "album_artist",
    "albumartist",
    "comment",
    "description",
    "synopsis",
    "copyright",
    "encoded_by",
    "encodedby",
    "publisher",
    "composer",
    "software",
    "title",
)

METHOD_PHRASE_RE = re.compile(
    r"(?:\b(?:patched|processed|compressed|encoded|edited)\s+by\b|"
    r"\b(?:patcher|method)\b|\bcompress(?:ed|ion|or|base)?\b)",
    re.IGNORECASE,
)
METHOD_DOMAIN_RE = re.compile(
    r"(?<![\w@])(?:https?://)?(?:www\.)?"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
    re.IGNORECASE,
)
IGNORED_METHOD_DOMAINS = (
    "tiktok.com",
    "tiktokcdn.com",
    "tiktokv.com",
    "muscdn.com",
)

# QuickTime/iTunes and 3GPP metadata atoms which commonly hold branding or
# patcher text. Some encoders use a standard `data` child; others expose the
# same values to ffprobe as format/stream tags.
METHOD_ATOM_TYPES = (
    (b"\xa9ART", True),   # Artist
    (b"\xa9cmt", False),  # Comment
    (b"desc", False),     # Description
    (b"ldes", False),     # Long description
    (b"cprt", False),     # Copyright
    (b"\xa9too", False),  # Encoder/tool
    (b"auth", False),     # 3GPP author
)


def _clean_method_text(value: object) -> str:
    """Normalize a metadata value while preserving the text stored in the video."""
    if value is None:
        return ""

    text = "".join(
        character
        for character in str(value).replace("\x00", "")
        if character in "\t\n\r" or ord(character) >= 32
    )
    text = " ".join(text.split()).strip()
    if not text:
        return ""

    if text.casefold() in {"not detected", "unknown", "none", "n/a"}:
        return ""

    # Keep the Telegram report safe/readable if a malformed file contains an
    # unexpectedly huge metadata field.
    return text[:256]


def _looks_like_method_text(value: str) -> bool:
    """Return True for an actual patcher/method marker, not generic media tags."""
    text = _clean_method_text(value)
    if not text:
        return False

    if METHOD_PHRASE_RE.search(text):
        return True

    for match in METHOD_DOMAIN_RE.finditer(text):
        domain = re.sub(r"^https?://", "", match.group(0), flags=re.IGNORECASE)
        domain = domain.removeprefix("www.").casefold()
        if not any(domain == blocked or domain.endswith(f".{blocked}") for blocked in IGNORED_METHOD_DOMAINS):
            return True

    return False


def _extract_method_from_tags(tags: object) -> str:
    if not isinstance(tags, dict):
        return ""

    normalized = {str(key).casefold(): value for key, value in tags.items()}

    # Preserve the existing behavior for explicit Method and Artist fields.
    for key in PRIMARY_METHOD_TAG_KEYS:
        value = _clean_method_text(normalized.get(key))
        if value:
            return value

    # Lower-confidence fields are accepted only when their value looks like a
    # real patcher/method marker. This prevents a normal title or description
    # from being reported as the Method.
    for key in SECONDARY_METHOD_TAG_KEYS:
        value = _clean_method_text(normalized.get(key))
        if value and _looks_like_method_text(value):
            return value

    # Support custom mdta keys such as com.vendor.patch.method.
    for key, raw_value in normalized.items():
        if not any(token in key for token in ("method", "patch", "artist", "comment", "description")):
            continue
        value = _clean_method_text(raw_value)
        if value and _looks_like_method_text(value):
            return value

    return ""


def _extract_method_from_ffprobe(ffprobe_data: dict) -> str:
    """
    Read the actual Method text from video metadata.

    Priority:
      1. format.tags.method
      2. format.tags.artist
      3. comment/description/copyright/custom mdta tags containing a real
         patcher marker
      4. equivalent stream tags

    The patcher used by Theziess Method stores its value in the MP4 Artist
    metadata field (©ART), which ffprobe exposes as `artist`.
    """
    format_tags = (ffprobe_data.get("format") or {}).get("tags") or {}
    value = _extract_method_from_tags(format_tags)
    if value:
        return value

    for stream in ffprobe_data.get("streams") or []:
        stream_tags = (stream or {}).get("tags") or {}
        value = _extract_method_from_tags(stream_tags)
        if value:
            return value

    return ""


def _read_mp4_atom(data: bytes, start: int, limit: int) -> tuple[int, int] | None:
    """Return (header_size, end) for one validated MP4 atom."""
    if start < 0 or start + 8 > limit:
        return None

    size = int.from_bytes(data[start:start + 4], "big")
    header_size = 8

    if size == 1:
        if start + 16 > limit:
            return None
        size = int.from_bytes(data[start + 8:start + 16], "big")
        header_size = 16
    elif size == 0:
        size = limit - start

    if size < header_size or start + size > limit:
        return None
    return header_size, start + size


def _decode_metadata_payload(raw_text: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "utf-16-be", "utf-16-le", "latin1"):
        try:
            value = _clean_method_text(raw_text.decode(encoding))
        except (UnicodeDecodeError, UnicodeError):
            continue
        if value:
            return value
    return ""


def _extract_data_child_text(data: bytes, type_pos: int, limit: int) -> str:
    atom_start = type_pos - 4
    atom = _read_mp4_atom(data, atom_start, limit)
    if not atom:
        return ""

    _, atom_end = atom
    data_type_pos = data.find(b"data", type_pos + 4, atom_end)
    if data_type_pos < 4:
        return ""

    data_atom_start = data_type_pos - 4
    data_atom = _read_mp4_atom(data, data_atom_start, atom_end)
    if not data_atom:
        return ""

    data_header_size, data_atom_end = data_atom
    # `data` payload begins with 4-byte type/flags and 4-byte locale.
    text_start = data_atom_start + data_header_size + 8
    if text_start > data_atom_end:
        return ""

    return _decode_metadata_payload(data[text_start:data_atom_end])


def _top_level_moov_ranges(data: bytes) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    offset = 0

    while offset + 8 <= len(data):
        atom = _read_mp4_atom(data, offset, len(data))
        if not atom:
            break
        _, atom_end = atom
        if data[offset + 4:offset + 8] == b"moov":
            ranges.append((offset, atom_end))
        if atom_end <= offset:
            break
        offset = atom_end

    return ranges


def _extract_mp4_artist_atom(file_path: Path) -> str:
    """
    Fallback: inspect real MP4/QuickTime metadata atoms when ffprobe does not
    expose the tag. ©ART is highest priority, followed by comment,
    description, copyright, encoder, 3GPP and mdta-style data values.
    """
    try:
        data = file_path.read_bytes()
    except OSError:
        return ""

    for marker, is_primary in METHOD_ATOM_TYPES:
        search_from = 0
        while True:
            type_pos = data.find(marker, search_from)
            if type_pos < 0:
                break
            search_from = type_pos + len(marker)
            value = _extract_data_child_text(data, type_pos, len(data))
            if value and (is_primary or _looks_like_method_text(value)):
                return value

    # mdta metadata uses numeric ilst item types, so scan validated `data`
    # atoms inside moov and accept only text with a method/patcher signal.
    moov_ranges = _top_level_moov_ranges(data)
    for moov_start, moov_end in moov_ranges:
        search_from = moov_start
        while True:
            data_type_pos = data.find(b"data", search_from, moov_end)
            if data_type_pos < 0:
                break
            search_from = data_type_pos + 4
            if data_type_pos < 4:
                continue
            data_atom_start = data_type_pos - 4
            atom = _read_mp4_atom(data, data_atom_start, moov_end)
            if not atom:
                continue
            header_size, data_atom_end = atom
            text_start = data_atom_start + header_size + 8
            if text_start > data_atom_end:
                continue
            value = _decode_metadata_payload(data[text_start:data_atom_end])
            if value and _looks_like_method_text(value):
                return value

        # Some 3GPP/custom boxes store text directly rather than in `data`.
        # Restrict the fallback to printable strings inside moov and require a
        # method marker, so compressed video bytes are never misidentified.
        moov_payload = data[moov_start:moov_end]
        for match in re.finditer(rb"[\x20-\x7e]{4,256}", moov_payload):
            value = _clean_method_text(match.group(0).decode("ascii", errors="ignore"))
            if value and _looks_like_method_text(value):
                return value

    return ""


def detect_theziess_method(file_path: Path, ffprobe_data: dict) -> tuple[bool, str]:
    """
    Extract Method from the video itself instead of returning a hard-coded name.

    Examples:
      artist=TheziessMethod.site -> TheziessMethod.site
      artist=editingnews.com     -> editingnews.com
    """
    method_text = _extract_method_from_ffprobe(ffprobe_data)

    if not method_text:
        method_text = _extract_mp4_artist_atom(file_path)

    if method_text:
        return True, method_text

    return False, "Not detected"


def analyze_video(file_path: Path, source: str, downloaded: bool) -> VideoReport:
    require_command("ffprobe", "pkg install ffmpeg -y")

    if not file_path.exists():
        raise CheckerError(f"រកមិនឃើញ file: {file_path}")
    if not file_path.is_file():
        raise CheckerError(f"Path នេះមិនមែនជា file: {file_path}")

    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]

    print(f"{Color.MAGENTA}🔍 កំពុងវិភាគ...{Color.RESET}")
    result = run_command(command, capture_output=True, timeout=120)

    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise CheckerError(
            "ffprobe មិនអាចអានវីដេអូនេះបាន។"
            + (f"\nព័ត៌មាន៖ {detail[-500:]}" if detail else "")
        )

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise CheckerError("ffprobe បញ្ចេញ JSON មិនត្រឹមត្រូវ។") from exc

    streams = data.get("streams", [])
    format_info = data.get("format", {})
    video = pick_stream(streams, "video")
    audio = pick_stream(streams, "audio")

    if not video:
        raise CheckerError("File នេះមិនមាន video stream។")

    fps = parse_fraction(video.get("avg_frame_rate"))
    if fps <= 0:
        fps = parse_fraction(video.get("r_frame_rate"))

    width = safe_int(video.get("width"))
    height = safe_int(video.get("height"))
    duration = safe_float(video.get("duration")) or safe_float(format_info.get("duration"))

    file_size_bytes = file_path.stat().st_size
    file_size_mb = file_size_bytes / (1024 * 1024)

    video_bitrate = safe_float(video.get("bit_rate"))
    overall_bitrate = safe_float(format_info.get("bit_rate"))

    # Estimate bitrate when metadata does not contain it.
    if overall_bitrate <= 0 and duration > 0:
        overall_bitrate = (file_size_bytes * 8) / duration

    if video_bitrate <= 0:
        # Use overall bitrate as a fallback; it may include audio.
        video_bitrate = overall_bitrate

    video_kbps = video_bitrate / 1000 if video_bitrate > 0 else 0.0
    overall_kbps = overall_bitrate / 1000 if overall_bitrate > 0 else 0.0

    label = resolution_label(width, height)
    score = quality_score(fps, width, height, video_kbps)
    method_detected, method_name = detect_theziess_method(file_path, data)

    return VideoReport(
        source=source,
        file_name=file_path.name,
        fps=fps,
        width=width,
        height=height,
        resolution_label=label,
        video_bitrate_kbps=video_kbps,
        overall_bitrate_kbps=overall_kbps,
        video_codec=str(video.get("codec_name") or "Unknown"),
        audio_codec=str(audio.get("codec_name") or "No audio"),
        pixel_format=str(video.get("pix_fmt") or "Unknown"),
        duration_seconds=duration,
        file_size_mb=file_size_mb,
        quality_score=score,
        downloaded=downloaded,
        method_detected=method_detected,
        method_name=method_name,
    )


def process_source(source: str) -> VideoReport:
    if is_url(source):
        # IMPORTANT: do not use the system /tmp here. Hosting containers often
        # mount /tmp as a small tmpfs, which breaks large 2K/4K downloads.
        temp_root = get_temp_root()
        with tempfile.TemporaryDirectory(
            prefix="tiktok_checker_",
            dir=str(temp_root),
        ) as temp_name:
            temp_dir = Path(temp_name)
            file_path = download_video(source, temp_dir)
            return analyze_video(file_path, source=source, downloaded=True)

    file_path = Path(source).expanduser().resolve()
    return analyze_video(file_path, source=str(file_path), downloaded=False)


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "Unknown"
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def score_color(score: str) -> str:
    return {
        "EXCELLENT": Color.GREEN,
        "VERY GOOD": Color.GREEN,
        "GOOD": Color.CYAN,
        "FAIR": Color.YELLOW,
        "LOW": Color.RED,
    }.get(score, Color.RESET)


def display_report(report: VideoReport) -> None:
    print(f"\n{Color.CYAN}{Color.BOLD}{LINE}")
    print("📊 VIDEO QUALITY REPORT")
    print(f"{LINE}{Color.RESET}")
    print(f"🔗 Source       : {report.source}")
    print(f"📹 FPS          : {report.fps:.2f} fps")
    print(
        f"📐 Resolution   : {report.width}x{report.height} "
        f"({report.resolution_label})"
    )
    print(f"💾 Video bitrate: {report.video_bitrate_kbps:.0f} kbps")
    print(f"📦 Total bitrate: {report.overall_bitrate_kbps:.0f} kbps")
    print(f"🎞️ Video codec  : {report.video_codec}")
    print(f"🔊 Audio codec  : {report.audio_codec}")
    print(f"🎨 Pixel format : {report.pixel_format}")
    print(f"⏱️ Duration     : {format_duration(report.duration_seconds)}")
    print(f"📦 File size    : {report.file_size_mb:.2f} MB")
    method_status = f"✅ {report.method_name}" if report.method_detected else "❌ Not detected"
    print(f"🧩 Method       : {method_status}")
    print(LINE)
    color = score_color(report.quality_score)
    print(
        f"📝 Quality Score: {color}{Color.BOLD}{report.quality_score}"
        f"{Color.RESET} ({report.fps:.0f}fps)"
    )
    print(f"{Color.CYAN}{LINE}{Color.RESET}")


CSV_FIELDS = [
    "source",
    "file_name",
    "fps",
    "width",
    "height",
    "resolution",
    "resolution_label",
    "video_bitrate_kbps",
    "overall_bitrate_kbps",
    "video_codec",
    "audio_codec",
    "pixel_format",
    "duration_seconds",
    "file_size_mb",
    "quality_score",
    "downloaded",
    "method_detected",
    "method_name",
    "error",
]


def report_to_csv_row(report: VideoReport) -> dict[str, object]:
    row = asdict(report)
    row["resolution"] = report.resolution
    row["fps"] = round(report.fps, 3)
    row["video_bitrate_kbps"] = round(report.video_bitrate_kbps, 2)
    row["overall_bitrate_kbps"] = round(report.overall_bitrate_kbps, 2)
    row["duration_seconds"] = round(report.duration_seconds, 3)
    row["file_size_mb"] = round(report.file_size_mb, 3)
    return {field: row.get(field, "") for field in CSV_FIELDS}


def export_csv(reports: Iterable[VideoReport], output_path: Path) -> None:
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for report in reports:
            writer.writerow(report_to_csv_row(report))

    success(f"បាន Export CSV: {output_path}")


def load_batch_file(batch_path: Path) -> list[str]:
    batch_path = batch_path.expanduser().resolve()
    if not batch_path.exists():
        raise CheckerError(f"រកមិនឃើញ batch file: {batch_path}")

    sources: list[str] = []
    with batch_path.open("r", encoding="utf-8-sig") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            sources.append(line)

    if not sources:
        raise CheckerError("Batch file មិនមាន URL ឬ file path ទេ។")

    return sources


def failed_report(source: str, message: str) -> VideoReport:
    return VideoReport(
        source=source,
        file_name="",
        fps=0.0,
        width=0,
        height=0,
        resolution_label="",
        video_bitrate_kbps=0.0,
        overall_bitrate_kbps=0.0,
        video_codec="",
        audio_codec="",
        pixel_format="",
        duration_seconds=0.0,
        file_size_mb=0.0,
        quality_score="ERROR",
        downloaded=is_url(source),
        error=message,
    )


def process_batch(batch_path: Path, csv_path: Optional[Path]) -> int:
    sources = load_batch_file(batch_path)
    reports: list[VideoReport] = []

    print(f"\n📚 រកឃើញ {len(sources)} item(s) ក្នុង batch file។")

    for index, source in enumerate(sources, start=1):
        print(f"\n{Color.BOLD}[{index}/{len(sources)}] {source}{Color.RESET}")
        try:
            report = process_source(source)
            reports.append(report)
            display_report(report)
        except (CheckerError, KeyboardInterrupt) as exc:
            message = "បានបោះបង់ដោយអ្នកប្រើ" if isinstance(exc, KeyboardInterrupt) else str(exc)
            error(message)
            reports.append(failed_report(source, message))
            if isinstance(exc, KeyboardInterrupt):
                break

    if csv_path:
        export_csv(reports, csv_path)

    successful = sum(1 for item in reports if not item.error)
    failed = len(reports) - successful
    print(f"\n📋 សរុប៖ ជោគជ័យ {successful} | បរាជ័យ {failed}")
    return 0 if failed == 0 else 2


def compare_reports(first: VideoReport, second: VideoReport) -> None:
    def winner(a: float, b: float, higher_is_better: bool = True) -> str:
        if abs(a - b) < 0.001:
            return "ស្មើ"
        if higher_is_better:
            return "Video 1" if a > b else "Video 2"
        return "Video 1" if a < b else "Video 2"

    print(f"\n{Color.CYAN}{Color.BOLD}{LINE}")
    print("⚖️ VIDEO QUALITY COMPARISON")
    print(f"{LINE}{Color.RESET}")
    print(f"{'Metric':<19} {'Video 1':<16} {'Video 2':<16} Winner")
    print("-" * 70)
    print(
        f"{'FPS':<19} {first.fps:<16.2f} {second.fps:<16.2f} "
        f"{winner(first.fps, second.fps)}"
    )
    first_pixels = first.width * first.height
    second_pixels = second.width * second.height
    print(
        f"{'Resolution':<19} {first.resolution:<16} {second.resolution:<16} "
        f"{winner(first_pixels, second_pixels)}"
    )
    print(
        f"{'Video bitrate':<19} "
        f"{first.video_bitrate_kbps:<16.0f} "
        f"{second.video_bitrate_kbps:<16.0f} "
        f"{winner(first.video_bitrate_kbps, second.video_bitrate_kbps)}"
    )
    print(
        f"{'File size MB':<19} {first.file_size_mb:<16.2f} "
        f"{second.file_size_mb:<16.2f} "
        f"{winner(first.file_size_mb, second.file_size_mb)}"
    )
    print(
        f"{'Quality score':<19} {first.quality_score:<16} "
        f"{second.quality_score:<16}"
    )
    print(f"{Color.CYAN}{LINE}{Color.RESET}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download/analyze TikTok videos and report FPS, resolution, "
            "bitrate, codecs, duration, and quality score."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tiktok_checker.py 'https://www.tiktok.com/@user/video/123'\n"
            "  python tiktok_checker.py '/sdcard/Download/video.mp4'\n"
            "  python tiktok_checker.py --batch urls.txt --csv results.csv\n"
            "  python tiktok_checker.py --compare video1.mp4 video2.mp4\n"
        ),
    )

    parser.add_argument(
        "source",
        nargs="?",
        help="TikTok/video URL ឬ local video path",
    )
    parser.add_argument(
        "--batch",
        type=Path,
        help="Text file ដែលមាន URL/file path មួយក្នុងមួយបន្ទាត់",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("VIDEO_1", "VIDEO_2"),
        help="ប្រៀបធៀប URL ឬ local video ចំនួន 2",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="Export report ទៅ CSV",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="បិទពណ៌ ANSI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="TikTok Quality Checker 1.1.0",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.no_color or not sys.stdout.isatty():
        Color.disable()

    print_header()

    selected_modes = sum(
        [
            bool(args.source),
            bool(args.batch),
            bool(args.compare),
        ]
    )
    if selected_modes != 1:
        parser.error("ជ្រើសរើសតែមួយ៖ source, --batch ឬ --compare")

    try:
        if args.batch:
            return process_batch(args.batch, args.csv)

        if args.compare:
            first_source, second_source = args.compare
            print(f"1️⃣ {first_source}")
            first = process_source(first_source)
            display_report(first)

            print(f"\n2️⃣ {second_source}")
            second = process_source(second_source)
            display_report(second)

            compare_reports(first, second)

            if args.csv:
                export_csv([first, second], args.csv)
            return 0

        assert args.source is not None
        print(f"🔗 Source: {args.source}")
        report = process_source(args.source)
        display_report(report)

        if args.csv:
            export_csv([report], args.csv)
        return 0

    except KeyboardInterrupt:
        print()
        warning("បានបោះបង់ដោយអ្នកប្រើ។ Temp files ត្រូវបាន cleanup។")
        return 130
    except CheckerError as exc:
        error(str(exc))
        return 1
    except Exception as exc:
        error(f"មានកំហុសដែលមិនបានរំពឹងទុក: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
