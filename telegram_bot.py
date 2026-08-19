#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from html import escape
from pathlib import Path
from typing import Final

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from fps_api_server import start_fps_api_server

from tiktok_checker import (
    CheckerError,
    VideoReport,
    cleanup_stale_temp_directories,
    download_video,
    format_duration,
    get_temp_root,
    is_url,
    process_source,
    remove_managed_temp_directory,
)
from video_compressor import (
    CompressionError,
    CompressionResult,
    compress_for_telegram,
)

load_dotenv()

BOT_TOKEN: Final[str] = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

TELEGRAM_BOT_API_URL: Final[str] = (
    os.getenv("TELEGRAM_BOT_API_URL", "").strip().rstrip("/")
)
MAX_CONCURRENT_JOBS: Final[int] = max(
    1,
    int(os.getenv("MAX_CONCURRENT_JOBS", os.getenv("MAX_CONCURRENT_CHECKS", "1"))),
)
JOB_TIMEOUT_SECONDS: Final[int] = max(
    60,
    int(os.getenv("JOB_TIMEOUT_SECONDS", os.getenv("CHECK_TIMEOUT_SECONDS", "420"))),
)
LOCAL_API_MAX_UPLOAD_MB: Final[int] = min(
    2000,
    max(1, int(os.getenv("LOCAL_API_MAX_UPLOAD_MB", "1990"))),
)
AUTO_COMPRESS_FOR_CLOUD: Final[bool] = os.getenv(
    "AUTO_COMPRESS_FOR_CLOUD",
    "true",
).strip().casefold() not in {"0", "false", "no", "off"}
CLOUD_UPLOAD_TARGET_MB: Final[float] = min(
    48.0,
    max(1.0, float(os.getenv("CLOUD_UPLOAD_TARGET_MB", "47"))),
)
CLOUD_UPLOAD_LIMIT_MB: Final[float] = 49.0
COMPRESSION_MAX_SHORT_SIDE: Final[int] = max(
    240,
    int(os.getenv("COMPRESSION_MAX_SHORT_SIDE", "1080")),
)
COMPRESSION_PRESET: Final[str] = os.getenv(
    "COMPRESSION_PRESET",
    "medium",
).strip() or "medium"
COMPRESSION_TIMEOUT_SECONDS: Final[int] = max(
    300,
    int(os.getenv("COMPRESSION_TIMEOUT_SECONDS", "1800")),
)
MAX_CONCURRENT_COMPRESSIONS: Final[int] = max(
    1,
    int(os.getenv("MAX_CONCURRENT_COMPRESSIONS", "1")),
)
job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
compression_semaphore = asyncio.Semaphore(MAX_CONCURRENT_COMPRESSIONS)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tiktok-quality-bot")



def extract_url(text: str) -> str | None:
    match = re.search(r"https?://\S+", text)
    if not match:
        return None
    return match.group(0).rstrip(").,]}>\"'")


def quality_emoji(score: str) -> str:
    return {
        "EXCELLENT": "🟢",
        "VERY GOOD": "🟢",
        "GOOD": "🔵",
        "FAIR": "🟡",
        "LOW": "🔴",
    }.get(score, "⚪")



def get_resolution_label(width: int, height: int) -> str:
    """Return a simple display label for both portrait and landscape video."""
    short_side = min(width, height)

    if short_side >= 4320:
        return "8K"
    if short_side >= 2160:
        return "4K"
    if short_side >= 1440:
        return "2K"
    if short_side >= 1080:
        return "1080P"
    if short_side >= 720:
        return "720P"
    if short_side >= 480:
        return "480P"
    if short_side >= 360:
        return "360P"
    if short_side >= 240:
        return "240P"
    return f"{short_side}P"


def format_report(report: VideoReport) -> str:
    source = escape(report.source)
    resolution_label = get_resolution_label(report.width, report.height)

    return (
        "📊 <b>VIDEO QUALITY REPORT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <b>URL:</b> {source}\n\n"
        f"📹 <b>FPS:</b> {report.fps:.2f} FPS\n"
        f"📐 <b>Resolution:</b> {report.width}x{report.height} "
        f"({resolution_label})\n"
        f"💾 <b>Bitrate:</b> {report.video_bitrate_kbps / 1000:.2f} Mbps\n"
        f"🎞️ <b>Video Codec:</b> {escape(report.video_codec.upper())}\n"
        f"🔊 <b>Audio Codec:</b> {escape(report.audio_codec.upper())}\n"
        f"🎨 <b>Pixel Format:</b> {escape(report.pixel_format)}\n"
        f"⏱️ <b>Duration:</b> {format_duration(report.duration_seconds)}\n"
        f"📁 <b>File Size:</b> {report.file_size_mb:.2f} MB\n"
        f"🧩 <b>Method:</b> "
        f"{escape(report.method_name) if report.method_detected else '❌ Not detected'}\n\n"
        f"{quality_emoji(report.quality_score)} <b>Quality:</b> "
        f"{escape(report.quality_score)} ({report.fps:.0f} FPS)\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )



async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    url = extract_url(message.text or "")
    if not url or not is_url(url):
        await message.reply_text(
            "❌ URL មិនត្រឹមត្រូវ។\n\n"
            "ប្រើ៖\n<code>/check https://vt.tiktok.com/...</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    status = await message.reply_text(
        "⏳ <b>កំពុងពិនិត្យគុណភាព...</b>\n\n",
        
        parse_mode=ParseMode.HTML,
    )

    try:
        async with job_semaphore:
            report = await asyncio.wait_for(
                asyncio.to_thread(process_source, url),
                timeout=JOB_TIMEOUT_SECONDS,
            )
        await status.edit_text(format_report(report), parse_mode=ParseMode.HTML)
    except asyncio.TimeoutError:
        await status.edit_text("⌛ ការពិនិត្យលើសពេលកំណត់។ សូមសាកម្ដងទៀត។")
    except CheckerError:
        await status.edit_text(
            "❌ <b>មិនអាចពិនិត្យវីដេអូនេះបាន</b>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("Unhandled /check error")
        await status.edit_text("❌ មិនអាចដោះស្រាយ URL វីដេអូ TikTok បានទេ")


async def download_from_url(update: Update, url: str) -> None:
    message = update.effective_message
    if message is None:
        return

    if not is_url(url):
        await message.reply_text(
            "❌ URL មិនត្រឹមត្រូវ។\n\n"
            "ប្រើ៖\n<code>/download https://vt.tiktok.com/...</code>\n"
            "ឬផ្ញើ link តែមួយ។",
            parse_mode=ParseMode.HTML,
        )
        return

    status = await message.reply_text(
        "⏳ <b>កំពុង Download វីដេអូ...</b>\n"
        "សូមរង់ចាំរហូតដល់ Bot ផ្ញើ file។",
        parse_mode=ParseMode.HTML,
    )

    temp_dir: Path | None = None

    try:
        async with job_semaphore:
            # Keep large downloads on the server's persistent disk allocation,
            # not the small container /tmp tmpfs.
            temp_root = get_temp_root()
            temp_name = tempfile.mkdtemp(
                prefix="telegram_tiktok_download_",
                dir=str(temp_root),
            )
            temp_dir = Path(temp_name)

            try:
                video_path = await asyncio.wait_for(
                    asyncio.to_thread(download_video, url, temp_dir),
                    timeout=JOB_TIMEOUT_SECONDS,
                )

                original_size_mb = video_path.stat().st_size / (1024 * 1024)
                compression: CompressionResult | None = None

                if (
                    not TELEGRAM_BOT_API_URL
                    and original_size_mb > CLOUD_UPLOAD_TARGET_MB
                ):
                    if not AUTO_COMPRESS_FOR_CLOUD:
                        await status.edit_text(
                            "❌ វីដេអូនេះលើសទំហំសុវត្ថិភាពរបស់ Telegram "
                            "ហើយ automatic compression ត្រូវបានបិទ។"
                        )
                        return

                    await status.edit_text(
                        "🗜️ <b>កំពុងបង្ហាប់សម្រាប់ Telegram...</b>\n"
                        f"📦 Original: <code>{original_size_mb:.2f} MB</code>\n"
                        f"🎯 Target: <code>{CLOUD_UPLOAD_TARGET_MB:.0f} MB</code>\n"
                        "🎞️ រក្សា FPS ដើម និងគុណភាព 1080p អតិបរមា។",
                        parse_mode=ParseMode.HTML,
                    )

                    compressed_path = temp_dir / "video_telegram_high_quality.mp4"
                    async with compression_semaphore:
                        compression = await asyncio.wait_for(
                            asyncio.to_thread(
                                compress_for_telegram,
                                video_path,
                                compressed_path,
                                target_size_mb=CLOUD_UPLOAD_TARGET_MB,
                                maximum_size_mb=CLOUD_UPLOAD_LIMIT_MB,
                                max_short_side=COMPRESSION_MAX_SHORT_SIDE,
                                preset=COMPRESSION_PRESET,
                                timeout_seconds=COMPRESSION_TIMEOUT_SECONDS,
                            ),
                            timeout=COMPRESSION_TIMEOUT_SECONDS + 60,
                        )
                    video_path = compression.output_path

                size_mb = video_path.stat().st_size / (1024 * 1024)

                upload_limit_mb = (
                    LOCAL_API_MAX_UPLOAD_MB if TELEGRAM_BOT_API_URL else 49
                )
                if size_mb > upload_limit_mb:
                    api_name = (
                        "Telegram Local Bot API"
                        if TELEGRAM_BOT_API_URL
                        else "Telegram Bot API ផ្លូវការ"
                    )
                    await status.edit_text(
                        f"❌ វីដេអូនេះធំពេកសម្រាប់ {api_name}។\n"
                        f"📁 Size: {size_mb:.2f} MB\n"
                        f"📦 Limit ដែលបានកំណត់: {upload_limit_mb} MB"
                    )
                    return

                # In local mode python-telegram-bot sends a file:// URI. The
                # Bot API container sees this same path through a read-only
                # shared volume, so the video is not copied into a second
                # upload cache on the VPS.
                if TELEGRAM_BOT_API_URL:
                    temp_dir.chmod(0o755)
                    video_path.chmod(0o644)

                await status.edit_text(
                    f"📤 <b>កំពុងផ្ញើវីដេអូ...</b>\n"
                    f"📁 Size: <code>{size_mb:.2f} MB</code>",
                    parse_mode=ParseMode.HTML,
                )

                caption = (
                    "✅ <b>Download បានជោគជ័យ</b>\n"
                    f"📁 Size: <code>{size_mb:.2f} MB</code>\n"
                )
                if compression is not None:
                    caption += (
                        "🗜️ Original: "
                        f"<code>{compression.original_size_mb:.2f} MB</code>\n"
                        f"🎞️ FPS: <code>{compression.source_fps:.2f} → "
                        f"{compression.output_fps:.2f}</code>\n"
                        f"📐 Resolution: <code>{compression.source_width}x"
                        f"{compression.source_height} → {compression.output_width}x"
                        f"{compression.output_height}</code>\n"
                    )
                caption += "🗑️ Server temp files នឹងត្រូវលុបក្រោយផ្ញើចប់។"

                try:
                    await message.reply_video(
                        video=video_path,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        supports_streaming=True,
                        read_timeout=JOB_TIMEOUT_SECONDS,
                        write_timeout=JOB_TIMEOUT_SECONDS,
                        connect_timeout=60,
                        pool_timeout=60,
                    )
                except BadRequest:
                    # Some MP4 files cannot be sent as Telegram video; retry as document.
                    await message.reply_document(
                        document=video_path,
                        filename=video_path.name,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        read_timeout=JOB_TIMEOUT_SECONDS,
                        write_timeout=JOB_TIMEOUT_SECONDS,
                        connect_timeout=60,
                        pool_timeout=60,
                    )
            finally:
                # Runs after success, Telegram rejection, timeout, cancellation,
                # or any other error. This is the main disk-protection rule.
                deleted = remove_managed_temp_directory(temp_dir)
                if deleted:
                    logger.info("Deleted temporary video directory: %s", temp_dir)
                else:
                    logger.warning(
                        "Temporary video directory still exists: %s",
                        temp_dir,
                    )

        await status.delete()

    except asyncio.TimeoutError:
        await status.edit_text("⌛ Download ឬការផ្ញើ file លើសពេលកំណត់។")
    except CheckerError as exc:
        detail = escape(str(exc))[:3000]
        await status.edit_text(
            "❌ <b>មិនអាច Download វីដេអូនេះបាន</b>\n\n"
            f"<code>{detail}</code>",
            parse_mode=ParseMode.HTML,
        )
    except CompressionError as exc:
        detail = escape(str(exc))[:3000]
        await status.edit_text(
            "❌ <b>មិនអាចបង្ហាប់វីដេអូក្រោម 49 MB បាន</b>\n\n"
            f"<code>{detail}</code>",
            parse_mode=ParseMode.HTML,
        )
    except (NetworkError, TelegramError) as exc:
        logger.warning("Telegram file upload failed: %s", exc)

        if TELEGRAM_BOT_API_URL:
            await status.edit_text(
                "❌ Download បាន ប៉ុន្តែមិនអាចផ្ញើ file បាន។\n"
                "Local Bot API អាចមានបញ្ហា ឬ file ធំពេក។"
            )
        else:
            await status.edit_text(
                "❌ វីដេអូ Download បាន ប៉ុន្តែមិនអាចផ្ញើតាម Telegram Bot API ផ្លូវការបាន។\n"
                "File អាចធំពេក។ សម្រាប់ file ធំ ត្រូវប្រើ Telegram Local Bot API Server។"
            )
    except Exception:
        logger.exception("Unhandled download error")
        await status.edit_text("❌ មានកំហុសក្នុង Server ពេល Download។")


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    url = extract_url(message.text or "")
    await download_from_url(update, url or "")


async def plain_link_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    text = (message.text or "").strip()
    url = extract_url(text)
    if not url:
        return

    # A normal message containing a URL is treated as /download.
    await download_from_url(update, url)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Telegram update failed", exc_info=context.error)


def main() -> None:
    # A hard reboot can bypass a Python finally block. Clear only directories
    # owned by this bot before accepting new work; unrelated files are ignored.
    stale_directories = cleanup_stale_temp_directories(max_age_seconds=0)
    if stale_directories:
        logger.info(
            "Removed %d stale temporary directorie(s)",
            len(stale_directories),
        )

    # Start the HTTP FPS API in the same process as the Telegram bot.
    # PEACHY allocation port 3008 should point to this listener.
    try:
        start_fps_api_server()
    except OSError as exc:
        logger.error("Unable to start FPS API: %s", exc)

    if not BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN មិនទាន់បានកំណត់។ Copy .env.example ទៅ .env រួចបញ្ចូល token។"
        )

    builder = Application.builder().token(BOT_TOKEN)

    if TELEGRAM_BOT_API_URL:
        logger.info("Using Telegram Local Bot API: %s", TELEGRAM_BOT_API_URL)
        builder = (
            builder
            .base_url(f"{TELEGRAM_BOT_API_URL}/bot")
            .base_file_url(f"{TELEGRAM_BOT_API_URL}/file/bot")
            .local_mode(True)
        )
    else:
        logger.info("Using official Telegram Bot API")

    application = builder.build()

    # Only two commands:
    # /check <link>    -> analyze video quality
    # /download <link> -> download and send video
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("download", download_command))

    # Sending a plain URL without a command behaves like /download.
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, plain_link_download)
    )

    application.add_error_handler(error_handler)
    logger.info("Bot started: /check, /download, and plain-link download are enabled")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
