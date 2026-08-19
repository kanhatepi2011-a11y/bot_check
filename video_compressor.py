#!/usr/bin/env python3
"""Size-targeted FFmpeg compression for Telegram's cloud Bot API."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


MIB = 1024 * 1024


class CompressionError(RuntimeError):
    """A user-facing compression failure."""


@dataclass(frozen=True)
class VideoInfo:
    duration_seconds: float
    width: int
    height: int
    fps: float
    has_audio: bool


@dataclass(frozen=True)
class CompressionResult:
    output_path: Path
    original_size_mb: float
    output_size_mb: float
    source_width: int
    source_height: int
    output_width: int
    output_height: int
    source_fps: float
    output_fps: float


def _parse_rate(value: object) -> float:
    text = str(value or "").strip()
    if not text or text in {"N/A", "0/0"}:
        return 0.0
    try:
        return float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        try:
            return float(text)
        except ValueError:
            return 0.0


def _run(command: list[str], timeout_seconds: int) -> None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise CompressionError(f"រកមិនឃើញ {command[0]} នៅក្នុង hosting។") from exc
    except subprocess.TimeoutExpired as exc:
        raise CompressionError("ការបង្ហាប់វីដេអូលើសពេលកំណត់។") from exc
    except OSError as exc:
        raise CompressionError(f"មិនអាចដំណើរការ FFmpeg បាន៖ {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Unknown FFmpeg error").strip()
        raise CompressionError(detail[-1500:])


def probe_video(path: Path, timeout_seconds: int = 60) -> VideoInfo:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,width,height,avg_frame_rate,r_frame_rate",
        "-of",
        "json",
        str(path),
    ]

    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        raise CompressionError(f"មិនអាចអានព័ត៌មានវីដេអូបាន៖ {exc}") from exc

    if result.returncode != 0:
        raise CompressionError((result.stderr or "ffprobe failed").strip()[-1500:])

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CompressionError("ffprobe បានផ្ដល់ទិន្នន័យមិនត្រឹមត្រូវ។") from exc

    streams = payload.get("streams") or []
    video = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    if video is None:
        raise CompressionError("File នេះមិនមាន video stream។")

    duration = _parse_rate((payload.get("format") or {}).get("duration"))
    if duration <= 0:
        raise CompressionError("មិនអាចរក duration ពិតរបស់វីដេអូបាន។")

    fps = _parse_rate(video.get("avg_frame_rate"))
    if fps <= 0:
        fps = _parse_rate(video.get("r_frame_rate"))

    return VideoInfo(
        duration_seconds=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps,
        has_audio=any(
            stream.get("codec_type") == "audio" for stream in streams
        ),
    )


def fit_1080p_dimensions(
    width: int,
    height: int,
    max_short_side: int = 1080,
) -> tuple[int, int]:
    """Fit landscape to 1080p height or portrait to 1080p width."""
    if width <= 0 or height <= 0:
        raise CompressionError("Resolution របស់វីដេអូមិនត្រឹមត្រូវ។")

    limiting_side = height if width >= height else width
    if limiting_side <= max_short_side:
        return width - (width % 2), height - (height % 2)

    scale = max_short_side / limiting_side
    output_width = max(2, int(width * scale))
    output_height = max(2, int(height * scale))
    return output_width - (output_width % 2), output_height - (output_height % 2)


def calculate_target_bitrates(
    duration_seconds: float,
    target_size_mb: float,
    has_audio: bool,
    safety_factor: float = 0.96,
) -> tuple[int, int]:
    """Return video/audio rates in kbps for a reliable target file size."""
    if duration_seconds <= 0 or target_size_mb <= 0:
        raise CompressionError("Compression target មិនត្រឹមត្រូវ។")

    total_kbps = int(
        target_size_mb * MIB * 8 * safety_factor / duration_seconds / 1000
    )

    if not has_audio:
        return max(180, total_kbps), 0

    if total_kbps >= 1500:
        audio_kbps = 128
    elif total_kbps >= 700:
        audio_kbps = 96
    else:
        audio_kbps = 64

    return max(180, total_kbps - audio_kbps), audio_kbps


def _remove_pass_logs(prefix: Path) -> None:
    for path in prefix.parent.glob(f"{prefix.name}*"):
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass


def compress_for_telegram(
    source_path: Path,
    output_path: Path,
    *,
    target_size_mb: float = 47.0,
    maximum_size_mb: float = 49.0,
    max_short_side: int = 1080,
    preset: str = "medium",
    timeout_seconds: int = 1800,
) -> CompressionResult:
    """Two-pass H.264 compression that preserves the input frame rate."""
    source_path = Path(source_path).resolve()
    output_path = Path(output_path).resolve()
    if source_path == output_path:
        raise CompressionError("Source និង output path មិនអាចដូចគ្នា។")

    info = probe_video(source_path)
    output_width, output_height = fit_1080p_dimensions(
        info.width,
        info.height,
        max_short_side=max(240, max_short_side),
    )
    base_video_kbps, audio_kbps = calculate_target_bitrates(
        info.duration_seconds,
        target_size_mb,
        info.has_audio,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    scale_filter = (
        f"scale={output_width}:{output_height}:flags=lanczos,"
        "format=yuv420p"
    )
    pass_prefix = output_path.parent / "ffmpeg_telegram_pass"
    maximum_bytes = int(maximum_size_mb * MIB)
    video_kbps = base_video_kbps

    common_video = [
        "-map",
        "0:v:0",
        "-vf",
        scale_filter,
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-profile:v",
        "high",
        "-tag:v",
        "avc1",
        "-fps_mode",
        "passthrough",
    ]

    try:
        for attempt in range(2):
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass

            bitrate_args = ["-b:v", f"{video_kbps}k"]
            first_pass = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                *common_video,
                *bitrate_args,
                "-pass",
                "1",
                "-passlogfile",
                str(pass_prefix),
                "-an",
                "-sn",
                "-f",
                "null",
                os.devnull,
            ]
            _run(first_pass, timeout_seconds)

            second_pass = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                *common_video,
                *bitrate_args,
                "-pass",
                "2",
                "-passlogfile",
                str(pass_prefix),
                "-map_metadata",
                "0",
                "-sn",
            ]
            if info.has_audio:
                second_pass.extend(
                    ["-map", "0:a:0?", "-c:a", "aac", "-b:a", f"{audio_kbps}k"]
                )
            else:
                second_pass.append("-an")
            second_pass.extend(["-movflags", "+faststart", str(output_path)])
            _run(second_pass, timeout_seconds)

            output_bytes = output_path.stat().st_size
            if output_bytes <= maximum_bytes:
                break

            # Container overhead or an unusual source can exceed the first
            # estimate. Retry once at a bitrate derived from the measured size.
            ratio = maximum_bytes / output_bytes
            video_kbps = max(180, int(video_kbps * ratio * 0.94))
        else:
            raise CompressionError("មិនអាចបង្ហាប់វីដេអូឱ្យក្រោម 49 MB បាន។")
    finally:
        _remove_pass_logs(pass_prefix)

    if not output_path.is_file():
        raise CompressionError("FFmpeg មិនបានបង្កើត output video។")

    output_info = probe_video(output_path)
    return CompressionResult(
        output_path=output_path,
        original_size_mb=source_path.stat().st_size / MIB,
        output_size_mb=output_path.stat().st_size / MIB,
        source_width=info.width,
        source_height=info.height,
        output_width=output_info.width,
        output_height=output_info.height,
        source_fps=info.fps,
        output_fps=output_info.fps,
    )
