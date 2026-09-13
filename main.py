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
        "📥 **Media yuklash:**\n"
        "• YouTube, Instagram, TikTok havolasini yuboring.\n\n"
        "🎵 **Musiqa qidirish:**\n"
        "• Qo'shiq nomi yoki ijrochini yozib yuboring."
    )

def format_time(seconds):
    if not seconds:
        return "0:00"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"

def extract_video_id(url):
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) if match else None

# ==========================================
# VIDEO SIQISH (480p - FONDA)
# ==========================================

def compress_to_480p(input_path, output_path):
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i", input_path,
            "-vf", "scale=-2:480",
            "-vcodec", "libx264",
            "-crf", "28",
            "-preset", "ultrafast",
            "-acodec", "aac",
            "-b:a", "128k",
            output_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except Exception:
        pass
    return False

# ==========================================
# MUSIQA YUKLASH (KAFOLATLANGAN MANBALAR)
# ==========================================

def download_audio_guaranteed(video_url, output_path):
    v_id = extract_video_id(video_url)
    
    # 1. Rapid/Y2Mate API Orqali (Server IP blokini aylanib o'tadi)
    if v_id:
        apis = [
            f"https://api.vevioz.com/api/button/mp3/{v_id}",
            f"https://ytstream-download-youtube-videos.p.rapidapi.com/dl?id={v_id}"
        ]
        
        try:
            # MP3 Converter API
            res = requests.get(f"https://api.mp3youtube.cc/v2/converter?url={video_url}", timeout=10)
            if res.status_code == 200:
                data = res.json()
                d_link = data.get("url") or data.get("link")
                if d_link:
                    r = requests.get(d_link, stream=True, timeout=40)
                    if r.status_code == 200:
                        with open(output_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)
                        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                            return True
        except Exception:
            pass

        # 2. Cobalt API Servislari
        cobalt_instances = [
            "https://api.cobalt.tools/api/json",
            "https://cobalt-api.kwippy.com/api/json",
            "https://co.wuk.sh/api/json"
        ]
        for instance in cobalt_instances:
            try:
                payload = {"url": video_url, "downloadMode": "audio", "audioFormat": "mp3"}
                headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
                res = requests.post(instance, json=payload, headers=headers, timeout=10)
                if res.status_code == 200:
                    d_url = res.json().get("url")
                    if d_url:
                        r = requests.get(d_url, stream=True, timeout=40)
                        if r.status_code == 200:
                            with open(output_path, "wb") as f:
                                for chunk in r.iter_content(chunk_size=8192):
                                    f.write(chunk)
                            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                                return True
            except Exception:
                continue

    # 3. Zaxira: yt-dlp iOS/Android Client bilan
    try:
        ydl_opts = {
            "outtmpl": output_path,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "format": "bestaudio/best",
            "extractor_args": {"youtube": {"player_client": ["ios", "android"]}},
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except Exception:
        pass

    return False

# ==========================================
# VIDEO YUKLASH
# ==========================================

def download_video_guaranteed(url, output_path):
    cobalt_instances = [
        "https://api.cobalt.tools/api/json",
        "https://cobalt-api.kwippy.com/api/json",
        "https://co.wuk.sh/api/json"
    ]
    payload = {"url": url}
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

    for instance in cobalt_instances:
        try:
            res = requests.post(instance, json=payload, headers=headers, timeout=12)
            if res.status_code == 200:
                d_url = res.json().get("url")
                if d_url:
                    r = requests.get(d_url, stream=True, timeout=40)
                    if r.status_code == 200:
                        with open(output_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                f.write(chunk)
                        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                            return True
        except Exception:
            continue

    try:
        ydl_opts = {
            "outtmpl": output_path,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "format": "best[ext=mp4]/best",
            "extractor_args": {"youtube": {"player_client": ["android", "ios"]}}
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except Exception:
        pass

    return False

# ==========================================
# QIDIRUV TIZIMI
# ==========================================

def search_youtube(query):
    try:
        ydl_opts = {
            "quiet": True,
            "extract_flat": True,
            "skip_download": True,
            "no_warnings": True,
            "nocheckcertificate": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch10:{query}", download=False)
            results = []
            if "entries" in info and info["entries"]:
                for entry in info["entries"]:
                    results.append({
                        "id": entry.get("id"),
                        "title": entry.get("title", "Musiqa"),
                        "duration": entry.get("duration", 0),
                        "url": f"https://www.youtube.com/watch?v={entry.get('id')}"
                    })
                return results
    except Exception as e:
        logging.error(f"Qidiruv xatosi: {e}")
    return []

# ==========================================
# HANDLERS
# ==========================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # HAVOLA BO'LSA (VIDEO)
    if re.match(r"^https?://", text):
        status = await update.message.reply_text("⏳ Yuklanyapti...")
        job_id = str(uuid.uuid4())
        work_dir = os.path.join(DOWNLOAD_DIR, job_id)
        os.makedirs(work_dir, exist_ok=True)
        raw_path = os.path.join(work_dir, "raw.mp4")
        compressed_path = os.path.join(work_dir, "compressed.mp4")

        try:
            success = download_video_guaranteed(text, raw_path)

            if success and os.path.exists(raw_path):
                compressed = compress_to_480p(raw_path, compressed_path)
                final_file = compressed_path if compressed else raw_path

                with open(final_file, "rb") as video:
                    await update.message.reply_video(
                        video=video,
                        caption="@yuklatgbot orqali yuklab olindi"
                    )
                await status.delete()
            else:
                await status.edit_text("❌ Yuklab bo'lmadi.")
        except Exception:
            logging.exception("MEDIA XATOSI")
            await status.edit_text("❌ Xatolik yuz berdi.")
        finally:
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)
        return

    # QO'SHIQ QIDIRISH
    status = await update.message.reply_text("🔍 Qidirilmoqda...")
    try:
        entries = search_youtube(text)

        if not entries:
            await status.edit_text("❌ Topilmadi.")
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
                "url": entry["url"],
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
        await status.edit_text("❌ Xatolik yuz berdi.")

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
            await query.message.reply_text("❌ Qidiruv natijasi eskirgan.")
            return

        status = await query.message.reply_text("📥 Qo'shiq yuklanyapti...")
        job_id = str(uuid.uuid4())
        work_dir = os.path.join(DOWNLOAD_DIR, job_id)
        os.makedirs(work_dir, exist_ok=True)
        audio_file = os.path.join(work_dir, "song.mp3")

        try:
            success = download_audio_guaranteed(song_data["url"], audio_file)

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
