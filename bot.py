import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp

logging.basicConfig(level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Salom! Video yuklash uchun Instagram yoki YouTube havolasini yuboring.")

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if not ("youtube.com" in url or "youtu.be" in url or "instagram.com" in url):
        await update.message.reply_text("Iltimos, faqat YouTube yoki Instagram havolasini yuboring.")
        return

    status_msg = await update.message.reply_text("Video yuklanmoqda, kuting...")
    output_filename = f"video_{update.message.message_id}.mp4"

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_filename,
        'quiet': True,
        'max_filesize': 50 * 1024 * 1024, # 50MB cheklov
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        await update.message.reply_video(video=open(output_filename, 'rb'))
        await status_msg.delete()
        os.remove(output_filename)

    except Exception as e:
        await status_msg.edit_text("Videoni yuklashda xatolik yuz berdi yoki fayl o'lchami juda katta.")
        if os.path.exists(output_filename):
            os.remove(output_filename)

if __name__ == '__main__':
    # BU YERGA @BotFather'DAN OLGAN TOKENINGIZNI YOZING:
    BOT_TOKEN = "8758335086:AAExX40PXwUg_YH2xultYXuYWou4QtT_nJY"
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))
    app.run_polling()
