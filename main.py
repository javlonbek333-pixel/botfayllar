import os
import re
import uuid
import shutil
import logging
import requests
import subprocess

import static_ffmpeg
import yt_dlp

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# FFmpeg ulanishi
static_ffmpeg.add_paths()
ffmpeg_exe = "ffmpeg"
ffprobe_exe = "ffprobe"

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = "/tmp/videos"
TARGET_SIZE_MB = 35

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Assalomu alaykum!\n\n"
        "🎬 Instagram va YouTube havolasini yuboring, men videoni yuklab va siqib beraman."
    )

def get_size_mb(filename):
    if not os.path.exists(filename):
        return 0
    return os.path.getsize(filename) / (1024 * 1024)

def get_duration(filename):
    command = [
        ffprobe_exe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        filename
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        return float(result.stdout.strip())
    except Exception:
        return 60

def compress_video(input_file, output_file):
    duration = get_duration(input_file)
    if duration <= 0:
        duration = 60

    target_bits = TARGET_SIZE_MB * 8 * 1024 * 1024
    audio_bitrate = 128000
    video_bitrate = int((target_bits / duration) - audio_bitrate)

    if video_bitrate < 600000:
        video_bitrate = 600000
    if video_bitrate > 3000000:
        video_bitrate = 3000000

    command = [
        ffmpeg_exe, "-y", "-i", input_file,
        "-vf", "scale=-2:480",
        "-c:v", "libx264",
        "-b:v", str(video_bitrate),
        "-crf", "23",
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_file
    ]

    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise Exception("FFmpeg xatosi:\n" + result.stderr[-500:])

def download_via_cobalt(url, output_path):
    api_url = "https://api.cobalt.tools/api/json"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    payload = {
        "url": url,
        "videoQuality": "720"
    }
    
    res = requests.post(api_url, json=payload, headers=headers, timeout=15)
    data = res.json()
    
    if "url" in data:
        video_res = requests.get(data["url"], stream=True, timeout=30)
        with open(output_path, "wb") as f:
            for chunk in video_res.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    return False

def is_supported_url(url):
    return any(domain in url.lower() for domain in ["youtube.com", "youtu.be", "instagram.com"])

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    url = update.message.text.strip()

    if not re.match(r"^https?://", url) or not is_supported_url(url):
        await update.message.reply_text("❌ Noto'g'ri havola. YouTube yoki Instagram havolasini yuboring.")
        return

    status = await update.message.reply_text("⏳ Video tayyorlanmoqda...")
    job_id = str(uuid.uuid4())
    work_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(work_dir, exist_ok=True)

    input_file = os.path.join(work_dir, "downloaded.mp4")
    output_file = os.path.join(work_dir, "compressed.mp4")

    try:
        await status.edit_text("⬇️ Video yuklanmoqda...")

        # 1-Urinish: Cobalt API orqali blokirovkasiz yuklash
        success = False
        try:
            success = download_via_cobalt(url, input_file)
        except Exception as err:
            logging.warning(f"Cobalt ishlamadi: {err}")

        # 2-Urinish: Zaxira sifatida yt-dlp
        if not success or not os.path.exists(input_file):
            ydl_opts = {
                "format": "best[ext=mp4]/best",
                "outtmpl": input_file,
                "quiet": True,
                "no_warnings": True,
                "ffmpeg_location": ffmpeg_exe
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

        if not os.path.exists(input_file):
            raise Exception("Videoni yuklab bo'lmadi.")

        original_size = get_size_mb(input_file)

        await status.edit_text(f"✅ Video yuklandi ({original_size:.1f} MB).\n🔄 Hajmi siqilmoqda...")

        compress_video(input_file, output_file)
        compressed_size = get_size_mb(output_file)

        final_file = output_file if (compressed_size > 0 and os.path.exists(output_file)) else input_file
        final_size = compressed_size if final_file == output_file else original_size

        await status.edit_text("📤 Video Telegramga yuborilmoqda...")

        with open(final_file, "rb") as video:
            await update.message.reply_video(
                video=video,
                caption=(
                    f"🎬 Video tayyor!\n"
                    f"📦 Asl hajm: {original_size:.1f} MB\n"
                    f"📉 Siqilgan hajm: {final_size:.1f} MB\n"
                    f"✅ Sifat: 480p"
                ),
                supports_streaming=True
            )

        await status.delete()

    except Exception as e:
        logging.exception("BOT XATOSI")
        error_text = str(e)[-500:]
        try:
            await status.edit_text(f"❌ Xatolik yuz berdi:\n\n{error_text}")
        except Exception:
            pass

    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN topilmadi! Railway Variables bo'limini tekshiring.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))

    logging.info("BOT ISHLAYAPTI")
    app.run_polling()

if __name__ == "__main__":
    main()
