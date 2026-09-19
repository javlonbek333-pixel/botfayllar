import os
import uuid
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

from config import BOT_TOKEN, TEMP_DIR
from music_recognizer import extract_audio, recognize_music
from media_handler import compress_video, download_video_from_url, download_audio_by_title

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

os.makedirs(TEMP_DIR, exist_ok=True)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 Salom!\n"
        "Men sizga musiqa topishga yordam beraman 🎶 menga quyidagilardan birini yuboring:\n\n"
        "🎵 Qo'shiq yoki ijrochi nomi\n"
        "🔤 Qo'shiq matni\n"
        "🎙 Musiqa bilan ovozli xabar\n"
        "📹 Musiqa bilan video\n"
        "🔊 Audioyozuv\n"
        "🎥 Musiqa bilan videoxabar\n"
        "🔗 Instagram, Tik-Tok, YouTube va boshqa saytlarga video havola\n\n"
        "🕺 Rohatlaning!"
    )
    await update.message.reply_text(welcome_text)

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    status_msg = await msg.reply_text("⏳ Fayl va havola ishlanmoqda...")

    session_id = str(uuid.uuid4())[:8]
    raw_video = os.path.join(TEMP_DIR, f"raw_{session_id}.mp4")
    compressed_video = os.path.join(TEMP_DIR, f"comp_{session_id}.mp4")
    audio_path = os.path.join(TEMP_DIR, f"audio_{session_id}.mp3")

    download_success = False

    try:
        if msg.text and msg.text.startswith("http"):
            download_success = download_video_from_url(msg.text, raw_video)
        elif msg.video or msg.video_note:
            media_obj = msg.video or msg.video_note
            file = await context.bot.get_file(media_obj.file_id)
            await file.download_to_drive(raw_video)
            download_success = True

        if not download_success or not os.path.exists(raw_video):
            await status_msg.edit_text("❌ Videoni yuklab bo'lmadi yoki havola xato.")
            return

        extract_audio(raw_video, audio_path)
        music_info = recognize_music(audio_path)
        compress_video(raw_video, compressed_video)

        caption = "📹 Video yuklandi va qayta ishlandi."
        keyboard = []

        if music_info:
            artist = music_info.get('artist', 'Noma\'lum')
            title = music_info.get('title', 'Noma\'lum')
            song_full_name = f"{artist} - {title}"
            caption += f"\n\n🎶 **Topilgan musiqa:**\n👤 {song_full_name}"
            
            cb_data = f"dl_{song_full_name}"[:60]
            keyboard.append([InlineKeyboardButton("📥 Musiqani yuklab olish", callback_data=cb_data)])
        else:
            caption += "\n\n❓ Musiqa aniqlanmadi."

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        target_video = compressed_video if os.path.exists(compressed_video) else raw_video
        with open(target_video, 'rb') as video_file:
            await msg.reply_video(
                video=video_file,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )

        await status_msg.delete()

    except Exception as e:
        logging.error(f"Xatolik: {e}")
        await status_msg.edit_text("❌ Xatolik yuz berdi.")

    finally:
        for p in [raw_video, compressed_video, audio_path]:
            if os.path.exists(p):
                os.remove(p)

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("dl_"):
        song_query = query.data.replace("dl_", "")
        info_msg = await query.message.reply_text(f"🔍 `{song_query}` yuklanmoqda...", parse_mode="Markdown")

        session_id = str(uuid.uuid4())[:8]
        out_mp3 = os.path.join(TEMP_DIR, f"song_{session_id}.mp3")

        if download_audio_by_title(song_query, out_mp3):
            actual_file = out_mp3 if os.path.exists(out_mp3) else f"{out_mp3.replace('.mp3', '')}.mp3"
            if os.path.exists(actual_file):
                with open(actual_file, 'rb') as audio_f:
                    await query.message.reply_audio(audio=audio_f, title=song_query)
                await info_msg.delete()
                os.remove(actual_file)
                return

        await info_msg.edit_text("❌ Musiqani yuklab bo'lmadi.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT | filters.VIDEO | filters.VIDEO_NOTE, handle_media))
    app.add_handler(CallbackQueryHandler(handle_button))

    print("🚀 Bot muvaffaqiyatli ishga tushdi...")
    app.run_polling()

if __name__ == "__main__":
    main()
