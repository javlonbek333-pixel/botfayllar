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
        "📥 **Media yuklash (480p):**\n"
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
# VIDEO SIQISH (480p)
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
        logging.error(f"480p siqish xatosi: {e}")
    return False

# ==========================================
# YOUTUBE SIZ / BOSHQA SAYTLARDAN MUSIQA QIDIRISH
# ==========================================

def search_external_music(query):
    results = []
    
    # 1. Jamendo / Ochiq MP3 bazalaridan qidiruv API
    try:
        url = f"https://api.jamendo.com/v3.0/tracks/?client_id=56d30fb7&format=json&limit=10&search={query}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json().get("results", [])
            for item in data:
                results.append({
                    "id": str(item.get("id")),
                    "title": f"{item.get('artist_name')} - {item.get('name')}",
                    "duration": item.get("duration", 0),
                    "download_url": item.get("audio")
                })
    except Exception:
        pass

    # 2. Zaxira: Invidious Proxy orqali MP3 URL olish
    if not results:
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
                    for item in res.json()[:10]:
                        results.append({
                            "id": item.get("videoId"),
                            "title": item.get("title"),
                            "duration": item.get("lengthSeconds", 0),
                            "download_url": f"https://www.youtube.com/watch?v={item.get('videoId')}"
                        })
                    if results:
                        break
            except Exception:
                continue

    return results

# ==========================================
# DIRECT DOWNLOAD (TO'G'RIDAN-TO'G'RI YUKLASH)
# ==========================================

def download_file_direct(url, output_path):
    # Agar to'g'ridan-to'g'ri MP3 havolasi bo'lsa
    if url.endswith(".mp3") or "jamendo" in url:
        try:
            res = requests.get(url, stream=True, timeout=30)
            if res.status_code == 200:
                with open(output_path, "wb") as f:
                    for chunk in res.iter_content(chunk_size=8192):
                        f.write(chunk)
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return True
        except Exception:
            pass

    # Cobalt API (Muqobil API)
    cobalt_instances = [
        "https://api.cobalt.tools/api/json",
        "https://co.wuk.sh/api/json"
    ]
    payload = {"url": url, "downloadMode": "audio", "audioFormat": "mp3"}
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    for instance in cobalt_instances:
        try:
            res = requests.post(instance, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                d_url = res.json().get("url")
                if d_url:
                    r = requests.get(d_url, stream=True, timeout=30)
                    with open(output_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        return True
        except Exception:
            continue

    return False

# ==========================================
# TEXT HANDLER
# ==========================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # HAVOLA BO'LSA (VIDEO)
    if re.match(r"^https?://", text):
        status = await update.message.reply_text("⏳ Video yuklanmoqda...")
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

    # QO'SHIQ QIDIRISH (BOSHQA SAYTLARDAN)
    status = await update.message.reply_text("🔍 Musiqa bazasidan qidirilmoqda...")
    try:
        entries = search_external_music(text)

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

            msg_text += f"{idx}. {title} **{duration}**\n"
            
            song_id = f"{update.message.message_id}_{idx}"
            context.user_data["search_results"][song_id] = {
                "download_url": entry["download_url"],
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
            success = download_file_direct(song_data["download_url"], audio_file)

            if success and os.path.exists(audio_file):
                with open(audio_file, "rb") as audio:
                    await query.message.reply_audio(
                        audio=audio,
                        title=song_data["title"],
                        caption="@yuklatgbot orqali yuklab olindi"
                    )
                await status.delete()
            else:
                await status.edit_text("❌ Qo'shiqni yuklab bo'lmadi. Boshqa nom bilan qidirib ko'ring.")

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
