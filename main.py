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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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

def format_time(seconds):
    if not seconds:
        return "0:00"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"

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

    # AGAR HAVOLA BO'LSA (URL)
    if re.match(r"^https?://", text):
        status = await update.message.reply_text("⏳ Media yuklanmoqda...")
        job_id = str(uuid.uuid4())
        work_dir = os.path.join(DOWNLOAD_DIR, job_id)
        os.makedirs(work_dir, exist_ok=True)
        file_path = os.path.join(work_dir, "media.mp4")

        try:
            ydl_opts = {
                "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "outtmpl": file_path,
                "quiet": True,
                "no_warnings": True,
                "extractor_args": {"youtube": {"player_client": ["android", "ios"]}}
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([text])

            if os.path.exists(file_path):
                with open(file_path, "rb") as video:
                    await update.message.reply_video(video=video, caption="@yuklatgbot orqali yuklab olindi")
                await status.delete()
            else:
                await status.edit_text("❌ Ushbu mediani yuklab bo'lmadi.")
        except Exception:
            await status.edit_text("❌ Yuklashda xatolik yuz berdi.")
        finally:
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)
        return

    # QIDIRUV (QO'SHIQ NOMI / IJROCHI)
    status = await update.message.reply_text("🔍 Qo'shiqlar qidirilmoqda...")
    try:
        ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "default_search": "ytsearch10:",
            "noplaylist": True,
            "skip_download": True,
            "ignoreerrors": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web"]
                }
            }
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(text, download=False)
            entries = info.get("entries", []) if info else []

            # Bo'sh yoki None obyektlarni tozalash
            valid_entries = [e for e in entries if e and isinstance(e, dict)]

            if not valid_entries:
                await status.edit_text("❌ Afsuski, hech qanday qo'shiq topilmadi.")
                return

            msg_text = f"🎧 **{text}** bo'yicha topilgan qo'shiqlar:\n\n"
            buttons = []
            row1, row2 = [], []

            if "search_results" not in context.user_data:
                context.user_data["search_results"] = {}

            for idx, entry in enumerate(valid_entries[:10], start=1):
                title = entry.get("title", "Noma'lum")
                duration = format_time(entry.get("duration", 0))
                url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={entry.get('id')}"

                msg_text += f"{idx}. {title} **{duration}**\n"
                
                song_id = f"{update.message.message_id}_{idx}"
                context.user_data["search_results"][song_id] = {
                    "url": url,
                    "title": title
                }

                btn = InlineKeyboardButton(str(idx), callback_data=f"dl:{song_id}")
                if idx <= 5:
                    row1.append(btn)
                else:
                    row2.append(btn)

            buttons.append(row1)
            if row2:
                buttons.append(row2)
            buttons.append([InlineKeyboardButton("❌", callback_data="cancel_search")])

            reply_markup = InlineKeyboardMarkup(buttons)
            await status.edit_text(msg_text, reply_markup=reply_markup, parse_mode="Markdown")

    except Exception as e:
        logging.exception("QIDIRUV XATOSI")
        await status.edit_text("❌ Qidiruvda xatolik yuz berdi. Biroq birozdan so'ng qayta urinib ko'ring.")

# ==========================================
# TUGMA BOSILGANDA MUSIQANI YUKLASH
# ==========================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_search":
        await query.message.delete()
        return

    if query.data.startswith("dl:"):
        song_id = query.data.split("dl:")[1]
        search_results = context.user_data.get("search_results", {})
        song_data = search_results.get(song_id)

        if not song_data:
            await query.message.reply_text("❌ Qidiruv natijasi eskirgan. Qaytadan qidiring.")
            return

        status = await query.message.reply_text(f"📥 **{song_data['title']}** yuklanmoqda...")
        job_id = str(uuid.uuid4())
        work_dir = os.path.join(DOWNLOAD_DIR, job_id)
        os.makedirs(work_dir, exist_ok=True)
        audio_file = os.path.join(work_dir, "song.mp3")

        try:
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(work_dir, "song.%(ext)s"),
                "quiet": True,
                "no_warnings": True,
                "ffmpeg_location": ffmpeg_exe,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android", "ios"]
                    }
                }
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([song_data["url"]])

            if not os.path.exists(audio_file):
                files = os.listdir(work_dir)
                if files:
                    audio_file = os.path.join(work_dir, files[0])

            if os.path.exists(audio_file):
                with open(audio_file, "rb") as audio:
                    await query.message.reply_audio(
                        audio=audio,
                        title=song_data["title"],
                        caption="@yuklatgbot orqali yuklab olindi"
                    )
                await status.delete()
            else:
                await status.edit_text("❌ Musiqani yuklab bo'lmadi.")

        except Exception as e:
            logging.exception("AUDIO YUKLASH XATOSI")
            await status.edit_text("❌ Musiqani yuklashda xatolik yuz berdi.")
        finally:
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)

# ==========================================
# MAIN
# ==========================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN topilmadi!")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE, handle_media_music))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logging.info("BOT ISHLAYAPTI")
    app.run_polling()

if __name__ == "__main__":
    main()
