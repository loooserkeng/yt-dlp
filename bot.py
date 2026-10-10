import asyncio
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("private-video-bot")

TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))
MAX_MB = int(os.environ.get("MAX_FILE_MB", "49"))
MAX_BYTES = MAX_MB * 1024 * 1024
LOCK = asyncio.Lock()
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}

def valid_url(text):
    try:
        p = urlparse(text.strip())
        return p.scheme == "https" and (p.hostname or "").lower() in YOUTUBE_HOSTS and not p.username and not p.password
    except ValueError:
        return False

def download(url, folder):
    opts = {
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[height<=1080]",
        "merge_output_format": "mp4",
        "outtmpl": str(Path(folder) / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "max_filesize": MAX_BYTES,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    outputs = [p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
    if not outputs:
        raise RuntimeError("No finished video file found.")
    result = max(outputs, key=lambda p: p.stat().st_size)
    if result.stat().st_size > MAX_BYTES:
        raise ValueError(f"Finished video is over the {MAX_MB} MB limit.")
    return result

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await update.effective_message.reply_text(f"Private bot is online. Send one YouTube URL. Up to 1080p when available; max file size {MAX_MB} MB.")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    msg = update.effective_message
    url = (msg.text or "").strip()
    if not valid_url(url):
        await msg.reply_text("Please send one HTTPS YouTube video URL (not a playlist).")
        return
    if LOCK.locked():
        await msg.reply_text("Another download is running. Try again shortly.")
        return
    async with LOCK:
        status = await msg.reply_text("Downloading; please wait...")
        try:
            with tempfile.TemporaryDirectory(prefix="video-") as tmp:
                path = await asyncio.to_thread(download, url, tmp)
                await status.edit_text("Download complete. Uploading to Telegram...")
                await context.bot.send_chat_action(msg.chat_id, ChatAction.UPLOAD_VIDEO)
                with path.open("rb") as f:
                    await msg.reply_video(video=f, caption=path.name, read_timeout=180, write_timeout=180, connect_timeout=30, pool_timeout=30)
            await status.delete()
        except Exception as e:
            log.exception("Download failed")
            await status.edit_text("Download failed. The video may be unavailable, too large, or unsupported. Details: " + str(e)[:500])

async def main():
    if not TOKEN:
        raise RuntimeError("Missing BOT_TOKEN (or TELEGRAM_BOT_TOKEN) Railway variable.")
    if MAX_MB < 1 or MAX_MB > 49:
        raise RuntimeError("MAX_FILE_MB must be between 1 and 49.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    log.info("Private Telegram bot starting")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
