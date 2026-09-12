import os
import re
import uuid
import shutil
import logging
import requests
import subprocess

import yt_dlp

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = "/tmp/bot_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)

# ==========================================
# /START BUYRUQI
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Assalomu aleykum, xush kelibsiz!\n\n"
        "📥 **Media yuklash:**\n"
        "• Instagram, TikTok, Facebook, YouTube, Pinterest havolasini yuboring.\n\n"
        "🎵 **Musiqa qidirish:**\n"
        "• Qo'shiq nomi yoki ijrochini yozib yuboring.\n\n"
        "🚀 Boshlash uchun havola yoki qo'shiq nomini yuboring!"
    )

def format_time(seconds):
    if not seconds:
        return "0:00"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"

# ==========================================
# VIDEO SIQISH (FFMPEG COMPRESSION)
# ==========================================

def compress_video(input_path, output_path):
    """FFmpeg yordamida videoni siqish va hajmini kichiklashtirish"""
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i", input_path,
            "-vcodec", "libx264",
            "-crf", "28",              # CRF 28 - hajmni tez va optimal siqish
            "-preset", "faster",       # Tezroq qayta ishlash
            "-acodec", "aac",
            "-b:a", "128k",
            output_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except Exception as e:
        logging.error(f"Video siqishda xatolik: {e}")
    return False

# ==========================================
# UNIVERSAL MEDIA YUKLOVCHI
# ==========================================

def download_media_cobalt(url, output_path):
    cobalt_instances = [
        "https://api.cobalt.tools/api/json",
        "https://cobalt-api.kwippy.com/api/json"
    ]
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    payload = {"url": url}

    for instance in cobalt_instances:
        try:
            res = requests.post(instance, json=payload, headers=headers, timeout=15)
            data = res.json()
            if "url" in data:
                media_res = requests.get(data["url"], stream=True, timeout=60)
                with open(output_path, "wb") as f:
                    for chunk in media_res.iter_content(chunk_size=8192):
                        f.write(chunk)
                return True
        except Exception:
            continue
    return False

# ==========================================
# MUSIQA QIDIRISH
# ==========================================

def search_youtube(query):
    try:
        ydl_opts = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "default_search": "ytsearch10",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            entries = info.get("entries", []) if info else []
            results = []
            for item in entries:
                if item and isinstance(item, dict):
                    results.append({
                        "id": item.get("id"),
                        "title": item.get("title", "Noma'lum"),
                        "duration": item.get("duration", 0),
                        "url": item.get("url") or f"https://www.youtube.com/watch?v={item.get('id')}"
                    })
            if results:
                return results
    except Exception:
        pass

    instances = [
        "https://invidious.drgns.space",
        "https://vid.puffyan.us",
        "https://inv.riverside.rocks"
    ]
    for instance in instances:
        try:
            url = f"{instance}/api/v1/search"
            params = {"q": query, "type": "video"}
            res = requests.get(url, params=params, timeout=5)
            if res.status_code == 200:
                data = res.json()
                results = []
                for item in data[:10]:
                    results.append({
                        "id": item.get("videoId"),
                        "title": item.get("title"),
                        "duration": item.get("lengthSeconds", 0),
                        "url": f"https://www.youtube.com/watch?v={item.get('videoId')}"
                    })
                if results:
                    return results
        except Exception:
            continue

    return []

def download_audio_cobalt(video_url, output_path):
    cobalt_instances = [
        "https://api.cobalt.tools/api/json",
        "https://cobalt-api.kwippy.com/api/json"
    ]
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    payload = {
        "url": video_url,
        "downloadMode": "audio",
        "audioFormat": "mp3"
    }

    for instance in cobalt_instances:
        try:
            res = requests.post(instance, json=payload, headers=headers, timeout=15)
            data = res.json()
            if "url" in data:
                audio_res = requests.get(data["url"], stream=True, timeout=30)
                with open(output_path, "wb") as f:
                    for chunk in audio_res.iter_content(chunk_size=8192):
                        f.write(chunk)
                return True
        except Exception:
            continue
    return False

# ==========================================
# TEXT HANDLER (HAVOLA YOKI QIDIRUV)
# ==========================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # 1. AGAR HAVOLA (URL) YUBORILGAN BO'LSA
    if re.match(r"^https?://", text):
        status = await update.message.reply_text("⏳ Media yuklanmoqda va siqilmoqda...")
        job_id = str(uuid.uuid4())
        work_dir = os.path.join(DOWNLOAD_DIR, job_id)
        os.makedirs(work_dir, exist_ok=True)
        raw_path = os.path.join(work_dir, "raw_media.mp4")
        compressed_path = os.path.join(work_dir, "compressed_media.mp4")

        try:
            # First Attempt: Cobalt API
            success = download_media_cobalt(text, raw_path)

            # Fallback: yt-dlp
            if not success or not os.path.exists(raw_path):
                ydl_opts = {
                    "format": "bestvideo+bestaudio/best",
                    "outtmpl": raw_path,
                    "quiet": True,
                    "no_warnings": True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([text])

            if os.path.exists(raw_path) and os.path.getsize(raw_path) > 0:
                # Video siqiladi
                compressed = compress_video(raw_path, compressed_path)
                final_file = compressed_path if compressed else raw_path

                with open(final_file, "rb") as video:
                    await update.message.reply_video(
                        video=video,
                        caption="@yuklatgbot orqali yuklab olindi"
                    )
                await status.delete()
            else:
                await status.edit_text("❌ Ushbu mediani yuklab bo'lmadi.")

        except Exception as e:
            logging.exception("MEDIA YUKLASH XATOSI")
            await status.edit_text("❌ Mediani yuklashda xatolik yuz berdi.")
        finally:
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)
        return

    # 2. AGAR MATN (QO'SHIQ NOMI) YUBORILGAN BO'LSA
    status = await update.message.reply_text("🔍 Qo'shiqlar qidirilmoqda...")
    try:
        entries = search_youtube(text)

        if not entries:
            await status.edit_text("❌ Afsuski, hech qanday qo'shiq topilmadi.")
            return

        msg_text = f"🎧 **{text}** bo'yicha topilgan qo'shiqlar:\n\n"
        buttons = []
        row1, row2 = [], []

        if "search_results" not in context.user_data:
            context.user_data["search_results"] = {}

        for idx, entry in enumerate(entries[:10], start=1):
            title = entry["title"]
            duration = format_time(entry["duration"])
            url = entry["url"]

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
        await status.edit_text("❌ Qidiruvda xatolik yuz berdi. Qayta urinib ko'ring.")

# ==========================================
# CALLBACK HANDLER
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
            success = download_audio_cobalt(song_data["url"], audio_file)

            if success and os.path.exists(audio_file):
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logging.info("BOT ISHLAYAPTI")
    app.run_polling()

if __name__ == "__main__":
    main()
