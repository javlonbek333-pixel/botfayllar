import os
import shutil
import logging
import subprocess
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from yt_dlp import YoutubeDL

# Logging sozlamalari
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Railway yoki Muhit o'zgaruvchilaridan tokenni olish
BOT_TOKEN = os.getenv("BOT_TOKEN")

# YouTube cookies sozlamasi (Railway Variables orqali kelsa faylga yozadi)
COOKIE_PATH = "cookies.txt"
cookies_env = os.getenv("YOUTUBE_COOKIES")
if cookies_env:
    with open(COOKIE_PATH, "w", encoding="utf-8") as f:
        f.write(cookies_env)

# FFmpeg orqali video davomiyligini aniqlash (sekundlarda)
def get_video_duration(input_path: str) -> float:
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        input_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        logger.error(f"Davomiylikni aniqlashda xato: {e}")
        return 0.0

# Videoni majburiy 480p ga va optimal hajmga siqish funksiyasi
def compress_video_to_480p(input_path: str, output_path: str):
    duration = get_video_duration(input_path)
    if duration <= 0:
        duration = 1.0

    # Maqsadli hajm: ~13.5 MB (Telegram cheklovlari va tezkor yuklanish uchun)
    target_bits = 13.5 * 1024 * 1024 * 8
    audio_bitrate = 128000  # 128 kbps audio
    video_total_bits = target_bits - (audio_bitrate * duration)

    if video_total_bits < 100000:
        video_total_bits = 100000

    video_bitrate = int(video_total_bits / duration)

    # scale='trunc(oh*a/2)*2:480' - balandlikni aniq 480p qiladi va kenglikni juft pikselga moslaydi (H.264 talabi)
    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-c:v', 'libx264',
        '-b:v', str(video_bitrate),
        '-preset', 'ultrafast',
        '-vf', "scale='trunc(oh*a/2)*2:480'",
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '128k',
        output_path
    ]
    
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

# /start komandasi
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Xush kelibsiz! Menga Instagram, YouTube yoki TikTok video havolasini yuboring.\n"
        "Men uni avtomatik ravishda **480p** formatga keltirib yuklab beraman!"
    )

# Havolalarni qayta ishlash va videoni yuklash
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        return

    user_id = update.message.from_user.id
    user_dir = f"/tmp/bot_downloads/{user_id}"
    os.makedirs(user_dir, exist_ok=True)

    status_msg = await update.message.reply_text("📥 Video haqida ma'lumot olinmoqda...")

    # yt-dlp sozlamalari
    ydl_opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best',
        'outtmpl': os.path.join(user_dir, 'raw_video.%(ext)s'),
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
    }

    # Cookies fayli bor bo'lsa qo'shish
    if os.path.exists(COOKIE_PATH):
        ydl_opts['cookiefile'] = COOKIE_PATH

    raw_file_path = None
    compressed_file_path = os.path.join(user_dir, "video_480p.mp4")

    try:
        await status_msg.edit_text("⬇️ Video yuklab olinmoqda...")
        
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            # Ba'zida kengaytma o'zgarishi mumkin bo'lgani uchun aniqlaymiz:
            base_name, _ = os.path.splitext(filename)
            if os.path.exists(base_name + ".mp4"):
                raw_file_path = base_name + ".mp4"
            else:
                raw_file_path = filename

        await status_msg.edit_text("⚙️ Video 480p formatga va mos hajmga siqilmoqda...")
        compress_video_to_480p(raw_file_path, compressed_file_path)

        await status_msg.edit_text("📤 Telegram'ga yuklanmoqda...")
        with open(compressed_file_path, 'rb') as video_file:
            await update.message.reply_video(
                video=video_file,
                caption="✅ Video 480p sifatda tayyorlandi."
            )
        
        await status_msg.delete()

    except Exception as e:
        logger.error(f"Xatolik yuz berdi: {e}")
        await status_msg.edit_text(f"❌ Videoni yuklashda xatolik yuz berdi yoki havola noto'g'ri.")

    finally:
        # Vaqtinchalik yaratilgan barcha fayllarni o'chirib tashlash
        if os.path.exists(user_dir):
            shutil.rmtree(user_dir, ignore_errors=True)

# Asosiy ishga tushirish funksiyasi
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN muhit o'zgaruvchisi topilmadi!")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot muvaffaqiyatli ishga tushdi...")
    app.run_polling()

if __name__ == '__main__':
    main()
