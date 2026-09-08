import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp

logging.basicConfig(level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Salom! Video yuklash uchun Instagram yoki YouTube havolasini yuboring.")

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not any(domain in url for domain in ["youtube.com", "youtu.be", "instagram.com"]):
        await update.message.reply_text("Iltimos, faqat YouTube yoki Instagram havolasini yuboring.")
        return

    status_msg = await update.message.reply_text("🎬 Video yuklanmoqda, biroz kuting...")
    output_filename = f"video_{update.message.message_id}.mp4"

    ydl_opts = {
        'format': 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best',
        'outtmpl': output_filename,
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android']
            }
        }
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        bot_username = (await context.bot.get_me()).username
        caption_text = f"🎬 Video @{bot_username} orqali yuklab olindi."

        await update.message.reply_video(
            video=open(output_filename, 'rb'),
            caption=caption_text
        )
        await status_msg.delete()
        os.remove(output_filename)

    except Exception as e:
        logging.error(f"Xatolik: {e}")
        await status_msg.edit_text("⚠️ Videoni yuklashda xatolik yuz berdi. Manzilni tekshirib qayta yuboring.")
        if os.path.exists(output_filename):
            os.remove(output_filename)

if __name__ == '__main__':
    BOT_TOKEN = "8758335086:AAExX40PXwUg_YH2xultYXuYWou4QtT_nJY"
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))
    app.run_polling(drop_pending_updates=True)
