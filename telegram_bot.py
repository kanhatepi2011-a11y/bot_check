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

from tiktok_checker import (
    CheckerError,
    VideoReport,
    download_video,
    format_duration,
    is_url,
    process_source,
)

load_dotenv()

BOT_TOKEN: Final[str] = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
MAX_CONCURRENT_JOBS: Final[int] = max(
    1,
    int(os.getenv("MAX_CONCURRENT_JOBS", os.getenv("MAX_CONCURRENT_CHECKS", "2"))),
)
JOB_TIMEOUT_SECONDS: Final[int] = max(
    60,
    int(os.getenv("JOB_TIMEOUT_SECONDS", os.getenv("CHECK_TIMEOUT_SECONDS", "420"))),
)
ALLOWED_USER_IDS: Final[set[int]] = {
    int(item.strip())
    for item in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if item.strip().isdigit()
}

job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tiktok-quality-bot")


def authorized(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    user = update.effective_user
    return bool(user and user.id in ALLOWED_USER_IDS)


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
        f"📁 <b>File Size:</b> {report.file_size_mb:.2f} MB\n\n"
        f"{quality_emoji(report.quality_score)} <b>Quality:</b> "
        f"{escape(report.quality_score)} ({report.fps:.0f} FPS)\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


async def reject_unauthorized(update: Update) -> bool:
    if authorized(update):
        return False
    if update.effective_message:
        await update.effective_message.reply_text("⛔ អ្នកមិនមានសិទ្ធិប្រើ bot នេះទេ។")
    return True


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or await reject_unauthorized(update):
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
    if message is None or await reject_unauthorized(update):
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

    try:
        async with job_semaphore:
            with tempfile.TemporaryDirectory(prefix="telegram_tiktok_download_") as temp_name:
                temp_dir = Path(temp_name)
                video_path = await asyncio.wait_for(
                    asyncio.to_thread(download_video, url, temp_dir),
                    timeout=JOB_TIMEOUT_SECONDS,
                )

                size_mb = video_path.stat().st_size / (1024 * 1024)
                await status.edit_text(
                    f"📤 <b>កំពុងផ្ញើវីដេអូ...</b>\n"
                    f"📁 Size: <code>{size_mb:.2f} MB</code>",
                    parse_mode=ParseMode.HTML,
                )

                caption = (
                    "✅ <b>Download បានជោគជ័យ</b>\n"
                    f"📁 Size: <code>{size_mb:.2f} MB</code>\n"
                    "🗑️ Server temp file នឹងត្រូវលុបក្រោយផ្ញើចប់។"
                )

                with video_path.open("rb") as video_file:
                    try:
                        await message.reply_video(
                            video=video_file,
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
                        video_file.seek(0)
                        await message.reply_document(
                            document=video_file,
                            filename=video_path.name,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            read_timeout=JOB_TIMEOUT_SECONDS,
                            write_timeout=JOB_TIMEOUT_SECONDS,
                            connect_timeout=60,
                            pool_timeout=60,
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
    except (NetworkError, TelegramError) as exc:
        logger.warning("Telegram file upload failed: %s", exc)
        await status.edit_text(
            "❌ Download បាន ប៉ុន្តែ Telegram មិនអាចទទួល file នេះបាន។\n"
            "File អាចធំពេក ឬ connection server មិនស្ថិរភាព។"
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
    if not BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN មិនទាន់បានកំណត់។ Copy .env.example ទៅ .env រួចបញ្ចូល token។"
        )

    application = Application.builder().token(BOT_TOKEN).build()

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
