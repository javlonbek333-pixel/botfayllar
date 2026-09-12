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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Assalomu aleykum!\n\n"
        "📥 **Media yuklash (480p formatda):**\n"
        "• Instagram, TikTok, Facebook, YouTube havolasini yuboring.\n\n"
        "🎵 **Musiqa qidirish:**\n"
        "• Qo'shiq nomi yoki ijrochini yozib yuboring."
    )

def format_time(seconds):
    if not seconds:
        return "0:00"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"

# ==========================================
# VIDEO SIQISH (480p FORMAT)
# ==========================================

def compress_to_480p(input_path, output_path):
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i", input_path,
            "-vf", "scale=-2:480",
            "-vcodec", "libx264",
            "-crf", "26",
            "-preset", "faster",
            "-acodec", "aac",
            "-b:a", "128k",
            output_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except Exception as e:
        logging.error(f"480p siqishda xatolik: {e}")
    return False

# ==========================================
# MUSIQANI ISHONCHLI YUKLASH (MULTI-SOURCE)
# ==========================================

def download_audio_fallback(url, output_path):
    # 1. Cobalt API tugunlari orqali yuklash
    cobalt_instances = [
        "https://api.cobalt.tools/api/json",
        "https://cobalt-api.kwippy.com/api/json",
        "https://co.wuk.sh/api/json"
    ]
    payload = {"url": url, "downloadMode": "audio", "audioFormat": "mp3"}
    headers = {
        "Accept": "application/json", 
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    for instance in cobalt_instances:
        try:
            res = requests.post(instance, json=payload, headers=headers, timeout=15)
            if res.status_code == 200:
                data = res.json()
                download_url = data.get("url")
                if download_url:
                    audio_res = requests.get(download_url, stream=True, timeout=40)
                    if audio_res.status_code == 200:
                        with open(output_path, "wb") as f:
                            for chunk in audio_res.iter_content(chunk_size=8192):
                                f.write(chunk)
                        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                            return True
        except Exception as e:
            logging.warning(f"Cobalt tugunida xatolik: {e}")
            continue

    # 2. Zaxira usul: yt-dlp orqali yuklash (Browser Emulation bilan)
    try:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_path.replace(".mp3", "") + ".%(ext)s",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": True,
            "no_warnings": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except Exception as e:
        logging.error(f"yt-dlp audio yuklashda xatolik: {e}")

    return False

# ==========================================
# MUSIQA QIDIRISH (BARQAROR INVIDIOUS)
# ==========================================

def search_youtube(query):
    # Public Invidious tugunlari orqali qidiruv
    instances = [
        "https://invidious.drgns.space",
        "https://vid.puffyan.us",
        "https://inv.riverside.rocks",
        "https://invidious.nerdvpn.de"
    ]
    for instance in instances:
        try:
            url = f"{instance}/api/v1/search"
            params = {"q": query, "type": "video"}
            res = requests.get(url, params=params, timeout=6)
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

    # Zaxira qidiruv: yt-dlp
    try:
        ydl_opts = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "default_search": "ytsearch10",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch10:{query}", download=False)
            entries = info.get("entries", []) if info else []
            results = []
            for item in entries:
                if item:
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

    return []

# ==========================================
# TEXT HANDLER
# ==========================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # HAVOLA BO'LSA (VIDEO)
    if re.match(r"^https?://", text):
        status = await update.message.reply_text("⏳ Video yuklanmoqda va 480p ga siqilmoqda...")
        job_id = str(uuid.uuid4())
        work_dir = os.path.join(DOWNLOAD_DIR, job_id)
        os.makedirs(work_dir, exist_ok=True)
        raw_path = os.path.join(work_dir, "raw.mp4")
        compressed_path = os.path.join(work_dir, "compressed_480p.mp4")

        try:
            ydl_opts = {
                "format": "bestvideo[height<=720]+bestaudio/best",
                "outtmpl": raw_path,
                "quiet": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([text])

            if os.path.exists(raw_path):
                compressed = compress_to_480p(raw_path, compressed_path)
                final_file = compressed_path if compressed else raw_path

                with open(final_file, "rb") as video:
                    await update.message.reply_video(
                        video=video,
                        caption="@yuklatgbot orqali yuklab olindi"
                    )
                await status.delete()
            else:
                await status.edit_text("❌ Mediani yuklab bo'lmadi.")
        except Exception:
            logging.exception("MEDIA XATOSI")
            await status.edit_text("❌ Video yuklashda xatolik yuz berdi.")
        finally:
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)
        return

    # QO'SHIQ QIDIRISH
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

    except Exception:
        logging.exception("QIDIRUV XATOSI")
        await status.edit_text("❌ Qidiruvda xatolik yuz berdi.")

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
            success = download_audio_fallback(song_data["url"], audio_file)

            if success and os.path.exists(audio_file):
                with open(audio_file, "rb") as audio:
                    await query.message.reply_audio(
                        audio=audio,
                        title=song_data["title"],
                        caption="@yuklatgbot orqali yuklab olindi"
                    )
                await status.delete()
            else:
                await status.edit_text("❌ Musiqani yuklab bo'lmadi. Qayta urinib ko'ring.")

        except Exception:
            logging.exception("AUDIO YUKLASH XATOSI")
            await status.edit_text("❌ Musiqani yuklashda xatolik yuz berdi.")
        finally:
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)

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
