import os
import re
import uuid
import shutil
import logging
import requests
import subprocess

import static_ffmpeg
import yt_dlp
from shazamio import Shazam

from telegram import Update, InputMediaPhoto
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
DOWNLOAD_DIR = "/tmp/bot_downloads"
TARGET_SIZE_MB = 35

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)

shazam = Shazam()

# ==========================================
# /START BUYRUQI
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Assalomu aleykum xush kelibsiz!\n\n"
        "@yuklatgbot orqali quyidagilarni yuklab olishingiz mumkin:\n\n"
        "• Instagram - post, stories, reels;\n"
        "• YouTube - video, shorts, audio;\n"
        "• Tik Tok - suv belgisiz video;\n"
        "• Facebook - reels;\n"
        "• Pinterest - rasm, video;\n"
        "• Snapchat - rasm, video;\n"
        "• Likee - rasm, video;\n"
        "• Threads - rasm, video.\n\n"
        "🎵 Qo'shiq topish bo'limi:\n"
        "• Qo'shiq nomi yoki ijrochini yozib yuboring;\n"
        "• Musiqa va audio/videolardan qo'shiqni aniqlash uchun faylni yuboring.\n\n"
        "🚀 Media yuklashni boshlash uchun uning havolasini yoki nomini yuboring."
    )

# ==========================================
# YORDAMCHI FUNKSIYALAR
# ==========================================

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

    subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def download_via_cobalt(url, download_dir):
    instances = [
        "https://api.cobalt.tools/api/json",
        "https://cobalt-api.kwippy.com/api/json"
    ]
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    payload = {"url": url, "videoQuality": "720", "downloadMode": "auto"}

    for api_url in instances:
        try:
            res = requests.post(api_url, json=payload, headers=headers, timeout=15)
            data = res.json()
            
            if data.get("status") == "redirect" or "url" in data:
                file_url = data.get("url")
                res_file = requests.get(file_url, stream=True, timeout=30)
                file_path = os.path.join(download_dir, "downloaded_file.mp4")
                with open(file_path, "wb") as f:
                    for chunk in res_file.iter_content(chunk_size=8192):
                        f.write(chunk)
                return {"status": "single", "path": file_path}
            
            elif data.get("status") == "picker":
                files = []
                for idx, item in enumerate(data.get("picker", [])):
                    item_url = item.get("url")
                    item_res = requests.get(item_url, stream=True, timeout=30)
                    ext = "jpg" if item.get("type") == "photo" else "mp4"
                    item_path = os.path.join(download_dir, f"item_{idx}.{ext}")
                    with open(item_path, "wb") as f:
                        for chunk in item_res.iter_content(chunk_size=8192):
                            f.write(chunk)
                    files.append({"path": item_path, "type": item.get("type")})
                return {"status": "picker", "files": files}
        except Exception:
            continue
    return None

# ==========================================
# MUSIQA ANIQLASH (VOICE/VIDEO/AUDIO)
# ==========================================

async def handle_media_music(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text("🔍 Musiqa aniqlanmoqda...")
    job_id = str(uuid.uuid4())
    work_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(work_dir, exist_ok=True)
    file_path = os.path.join(work_dir, "media_file")

    try:
        message = update.message
        media = message.voice or message.audio or message.video or message.video_note
        telegram_file = await context.bot.get_file(media.file_id)
        await telegram_file.download_to_drive(file_path)

        out = await shazam.recognize(file_path)
        track = out.get("track")

        if track:
            title = track.get("title", "Noma'lum")
            subtitle = track.get("subtitle", "Noma'lum")
            await status.edit_text(
                f"🎵 **Topilgan qo'shiq:**\n\n"
                f"👤 **Ijrochi:** {subtitle}\n"
                f"🎧 **Nomi:** {title}\n\n"
                f"@yuklatgbot orqali topildi",
                parse_mode="Markdown"
            )
        else:
            await status.edit_text("❌ Afsuski, ushbu fayldan musiqa topilmadi.")

    except Exception as e:
        logging.exception("SHAZAM XATOSI")
        await status.edit_text("❌ Musiqani aniqlashda xatolik yuz berdi.")
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

# ==========================================
# MATN BO'YICHA QIDIRUV / URL YUKLASH
# ==========================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if re.match(r"^https?://", text):
        status = await update.message.reply_text("⏳ Media yuklanmoqda...")
        job_id = str(uuid.uuid4())
        work_dir = os.path.join(DOWNLOAD_DIR, job_id)
        os.makedirs(work_dir, exist_ok=True)

        try:
            cobalt_result = download_via_cobalt(text, work_dir)

            if cobalt_result:
                if cobalt_result["status"] == "single":
                    file_path = cobalt_result["path"]
                    if file_path.endswith(".mp4"):
                        compressed_path = os.path.join(work_dir, "compressed.mp4")
                        compress_video(file_path, compressed_path)
                        final_file = compressed_path if os.path.exists(compressed_path) else file_path
                        
                        with open(final_file, "rb") as video:
                            await update.message.reply_video(
                                video=video,
                                caption="@yuklatgbot orqali yuklab olindi",
                                supports_streaming=True
                            )
                    else:
                        with open(file_path, "rb") as photo:
                            await update.message.reply_photo(
                                photo=photo,
                                caption="@yuklatgbot orqali yuklab olindi"
                            )
                    await status.delete()
                    return

                elif cobalt_result["status"] == "picker":
                    media_group = []
                    for item in cobalt_result["files"][:10]:
                        if item["path"].endswith((".jpg", ".png", ".jpeg")):
                            media_group.append(InputMediaPhoto(media=open(item["path"], "rb")))
                    if media_group:
                        await update.message.reply_media_group(media=media_group)
                    await status.delete()
                    return

            input_file = os.path.join(work_dir, "downloaded.mp4")
            ydl_opts = {
                "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "outtmpl": input_file,
                "quiet": True,
                "no_warnings": True,
                "ffmpeg_location": ffmpeg_exe,
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android", "ios"],
                        "player_skip": ["configs", "webpage"]
                    }
                }
            }

            if os.path.exists("cookies.txt"):
                ydl_opts["cookiefile"] = "cookies.txt"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([text])

            if os.path.exists(input_file):
                compressed_file = os.path.join(work_dir, "compressed.mp4")
                compress_video(input_file, compressed_file)
                final_file = compressed_file if os.path.exists(compressed_file) else input_file

                with open(final_file, "rb") as video:
                    await update.message.reply_video(
                        video=video,
                        caption="@yuklatgbot orqali yuklab olindi",
                        supports_streaming=True
                    )
                await status.delete()
            else:
                raise Exception("Media faylni yuklab bo'lmadi.")

        except Exception as e:
            logging.exception("YUKLASH XATOSI")
            await status.edit_text("❌ Ushbu mediani yuklab bo'lmadi. Havola ochiq (public) ekanligini tekshiring.")
        finally:
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)

    else:
        status = await update.message.reply_text(f"🔍 '{text}' bo'yicha qo'shiq qidirilmoqda...")
        try:
            ydl_opts = {
                "format": "bestaudio/best",
                "quiet": True,
                "default_search": "ytsearch1:",
                "noplaylist": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(text, download=False)
                if "entries" in info and len(info["entries"]) > 0:
                    first_result = info["entries"][0]
                    title = first_result.get("title")
                    webpage_url = first_result.get("webpage_url")
                    
                    await status.edit_text(
                        f"🎵 **Topilgan qo'shiq:**\n"
                        f"📌 [{title}]({webpage_url})\n\n"
                        f"📥 Yuklab olish uchun havola matnini qayta yuboring.",
                        parse_mode="Markdown"
                    )
                else:
                    await status.edit_text("❌ Afsuski, bunday nomli qo'shiq topilmadi.")
        except Exception as e:
            logging.exception("QIDIRUV XATOSI")
            await status.edit_text("❌ Qidiruvda xatolik yuz berdi.")

# ==========================================
# MAIN
# ==========================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN topilmadi! Railway Variables bo'limini tekshiring.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE, handle_media_music))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logging.info("BOT ISHLAYAPTI")
    app.run_polling()

if __name__ == "__main__":
    main()
