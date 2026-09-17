import os
import shutil
import asyncio
import subprocess
import json
import math
import hashlib
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# Logging sozlamalari
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot tokeningizni shu yerga yozing yoki muhit o'zgaruvchisidan oling
TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
BASE_DIR = "/tmp/bot_downloads"

# Xotirada vaqtinchalik saqlash joylari
SEARCH_RESULTS = {}
URL_CACHE = {}

def cache_url(url: str) -> str:
    """Telegramning 64 baytlik callback limitini chetlab o'tish uchun url'ni qisqa kalitga o'tkazish"""
    key = hashlib.md5(url.encode()).hexdigest()[:10]
    URL_CACHE[key] = url
    return key

def get_cached_url(key: str) -> str:
    return URL_CACHE.get(key)

def get_search_keyboard(user_id: int, page: int = 1):
    results = SEARCH_RESULTS.get(user_id, [])
    total_items = len(results)
    per_page = 10
    total_pages = math.ceil(total_items / per_page) or 1
    
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    current_page_items = results[start_idx:end_idx]
    
    keyboard = []
    for idx, item in enumerate(current_page_items, start=start_idx + 1):
        title = item.get('title', 'Nomaʼlum')
        if len(title) > 40:
            title = title[:37] + '...'
        keyboard.append([InlineKeyboardButton(f"{idx}. {title}", callback_data=f"sel_{idx}")])
    
    # Sahifalash tugmalari
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Orqaga", callback_data=f"page_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Oldinga ➡️", callback_data=f"page_{page + 1}"))
    
    if nav_row:
        keyboard.append(nav_row)
        
    return InlineKeyboardMarkup(keyboard), page

def get_video_duration(file_path: str) -> float:
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', file_path]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        return float(data['format']['duration'])
    except Exception:
        return 10.0

def compress_video_to_14mb(input_path: str, output_path: str):
    duration = get_video_duration(input_path)
    if duration <= 0:
        duration = 1.0
    
    # 14 MB = 14 * 1024 * 1024 * 8 bit
    target_bits = 14 * 1024 * 1024 * 8
    audio_bitrate = 128000 # 128 kbps
    video_total_bits = target_bits - (audio_bitrate * duration)
    if video_total_bits < 100000:
        video_total_bits = 100000
    video_bitrate = int(video_total_bits / duration)
    
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-c:v', 'libx264', '-b:v', str(video_bitrate),
        '-preset', 'ultrafast', '-vf', 'scale=-2:480',
        '-c:a', 'aac', '-b:a', '128k',
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Salom! Men media yuklovchi va qo'shiq qidiruvchi botman.\n\n"
        "• YouTube, Instagram, TikTok, Facebook, Pinterest va boshqa tarmoqlardan havola yuboring.\n"
        "• Yoki shunchaki qo'shiq/video nomini yuboring (YouTube'dan qidirib beraman)."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.message.from_user.id
    
    # Havola ekanligini aniqlash
    is_url = text.startswith("http://") or text.startswith("https://") or "youtu" in text or "t.me" in text or "tiktok" in text or "instagram" in text
    
    if is_url:
        if "youtube.com" in text or "youtu.be" in text:
            key = cache_url(text)
            keyboard = [
                [
                    InlineKeyboardButton("🎬 Video (480p)", callback_data=f"dl_vid_{key}"),
                    InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"dl_mp3_{key}")
                ]
            ]
            await update.message.reply_text("YouTube havolasi topildi. Formatni tanlang:", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            msg = await update.message.reply_text("⏳ Yuklab olinmoqda...")
            user_dir = os.path.join(BASE_DIR, str(user_id))
            os.makedirs(user_dir, exist_ok=True)
            
            try:
                ydl_opts = {
                    'format': 'bestvideo[height<=480]+bestaudio/best[height<=480]/best',
                    'outtmpl': os.path.join(user_dir, '%(id)s.%(ext)s'),
                    'max_filesize': 1024 * 1024 * 1024, # Max 1 GB
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(text, download=True)
                    filename = ydl.prepare_filename(info)
                    
                if not os.path.exists(filename):
                    files = os.listdir(user_dir)
                    if files:
                        filename = os.path.join(user_dir, files[0])
                    else:
                        raise Exception("Fayl topilmadi.")
                        
                file_size = os.path.getsize(filename)
                if file_size > 14 * 1024 * 1024 and filename.endswith(('.mp4', '.mkv', '.webm', '.mov')):
                    await msg.edit_text("⚙️ Fayl 14 MB dan katta, siqilmoqda...")
                    compressed_path = filename + "_compressed.mp4"
                    compress_video_to_14mb(filename, compressed_path)
                    filename = compressed_path
                    
                await update.message.reply_video(video=open(filename, 'rb'))
                await msg.delete()
            except Exception as e:
                logger.error(f"Xatolik: {e}")
                await msg.edit_text(f"❌ Xatolik yuz berdi: {str(e)}")
            finally:
                if os.path.exists(user_dir):
                    shutil.rmtree(user_dir, ignore_errors=True)
    else:
        # Qo'shiq/matnli qidiruv
        msg = await update.message.reply_text("🔍 Qidirilmoqda...")
        try:
            ydl_opts = {
                'extract_flat': True,
                'default_search': 'ytsearch50',
                'skip_download': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(text, download=False)
                entries = info.get('entries', [])
            
            if not entries:
                await msg.edit_text("❌ Hech narsa topilmadi.")
                return
            
            SEARCH_RESULTS[user_id] = entries
            keyboard, page = get_search_keyboard(user_id, page=1)
            await msg.edit_text(f"🔍 Qidiruv natijalari (Jami: {len(entries)} ta):", reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Qidiruv xatoligi: {e}")
            await msg.edit_text(f"❌ Qidirishda xatolik: {str(e)}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    
    if data.startswith("page_"):
        page = int(data.split("_")[1])
        keyboard, _ = get_search_keyboard(user_id, page=page)
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception:
            pass
        
    elif data == "noop":
        pass
        
    elif data.startswith("sel_"):
        idx = int(data.split("_")[1]) - 1
        entries = SEARCH_RESULTS.get(user_id, [])
        if idx < 0 or idx >= len(entries):
            await query.edit_message_text("❌ Natija eskirgan yoki topilmadi.")
            return
            
        entry = entries[idx]
        video_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
        
        await query.edit_message_text("🎵 Qo'shiq yuklab olinmoqda va MP3 ga o'tkazilmoqda...")
        user_dir = os.path.join(BASE_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        
        try:
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(user_dir, '%(id)s.%(ext)s'),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '128',
                }],
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                filename = ydl.prepare_filename(info)
                base, _ = os.path.splitext(filename)
                filename = base + ".mp3"
                
            if not os.path.exists(filename):
                files = os.listdir(user_dir)
                for f in files:
                    if f.endswith('.mp3'):
                        filename = os.path.join(user_dir, f)
                        break
                        
            await context.bot.send_audio(chat_id=user_id, audio=open(filename, 'rb'), title=entry.get('title', 'Audio'))
            await query.message.delete()
        except Exception as e:
            logger.error(f"Yuklash xatoligi: {e}")
            await query.edit_message_text(f"❌ Yuklashda xatolik: {str(e)}")
        finally:
            if os.path.exists(user_dir):
                shutil.rmtree(user_dir, ignore_errors=True)
                
    elif data.startswith("dl_vid_") or data.startswith("dl_mp3_"):
        parts = data.split("_", 2)
        action = parts[1] # vid yoki mp3
        key = parts[2]
        url = get_cached_url(key)
        
        if not url:
            await query.edit_message_text("❌ Havola eskirgan. Qaytadan yuboring.")
            return
            
        await query.edit_message_text("⏳ Yuklab olinmoqda...")
        user_dir = os.path.join(BASE_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        
        try:
            if action == "vid":
                ydl_opts = {
                    'format': 'bestvideo[height<=480]+bestaudio/best[height<=480]/best',
                    'outtmpl': os.path.join(user_dir, '%(id)s.%(ext)s'),
                    'max_filesize': 1024 * 1024 * 1024,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    
                if not os.path.exists(filename):
                    files = os.listdir(user_dir)
                    if files:
                        filename = os.path.join(user_dir, files[0])
                        
                file_size = os.path.getsize(filename)
                if file_size > 14 * 1024 * 1024 and filename.endswith(('.mp4', '.mkv', '.webm', '.mov')):
                    await query.edit_message_text("⚙️ Fayl 14 MB dan katta, siqilmoqda...")
                    compressed_path = filename + "_compressed.mp4"
                    compress_video_to_14mb(filename, compressed_path)
                    filename = compressed_path
                    
                await context.bot.send_video(chat_id=user_id, video=open(filename, 'rb'))
            else:
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': os.path.join(user_dir, '%(id)s.%(ext)s'),
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '128',
                    }],
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    base, _ = os.path.splitext(filename)
                    filename = base + ".mp3"
                    
                if not os.path.exists(filename):
                    files = os.listdir(user_dir)
                    for f in files:
                        if f.endswith('.mp3'):
                            filename = os.path.join(user_dir, f)
                            break
                            
                await context.bot.send_audio(chat_id=user_id, audio=open(filename, 'rb'))
            await query.message.delete()
        except Exception as e:
            logger.error(f"Xatolik: {e}")
            await query.edit_message_text(f"❌ Xatolik yuz berdi: {str(e)}")
        finally:
            if os.path.exists(user_dir):
                shutil.rmtree(user_dir, ignore_errors=True)

def main():
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Bot ishga tushdi...")
    application.run_polling()

if __name__ == '__main__':
    main()
