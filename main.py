import asyncio
import logging
import os
import re
import shutil
from pathlib import Path

import yt_dlp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# =========================================================
# SOZLAMALAR
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "BU_YERGA_BOT_TOKENINGIZNI_YOZING")

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

MAX_RESULTS = 10

# Telegram bot uchun xavfsizroq limit
MAX_FILE_SIZE_MB = 49


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# =========================================================
# BOT
# =========================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =========================================================
# YOUTUBE QIDIRUV
# =========================================================

def search_youtube(query: str, limit: int = 10):
    """
    YouTube'dan qo'shiq/video qidiradi.
    Yuklab olmaydi.
    """

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }

    search_query = f"ytsearch{limit}:{query}"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                search_query,
                download=False
            )

        results = []

        for item in info.get("entries", []):
            if not item:
                continue

            video_id = item.get("id")

            if not video_id:
                continue

            title = item.get("title") or "Noma'lum"

            duration = item.get("duration")

            uploader = item.get("uploader") or ""

            results.append(
                {
                    "id": video_id,
                    "title": title,
                    "duration": duration,
                    "uploader": uploader,
                }
            )

        return results

    except Exception as e:
        logger.exception("YouTube qidiruv xatosi: %s", e)
        return []


# =========================================================
# MATNNI TOZALASH
# =========================================================

def clean_filename(name: str) -> str:
    """
    Fayl nomidagi Windows/Linux uchun xavfli belgilarni olib tashlaydi.
    """

    name = re.sub(r'[\\/*?:"<>|]', "", name)

    name = name.strip()

    if not name:
        name = "audio"

    return name[:100]


# =========================================================
# VAQT FORMAT
# =========================================================

def format_duration(seconds):
    if not seconds:
        return ""

    try:
        seconds = int(seconds)
    except Exception:
        return ""

    minutes = seconds // 60
    seconds = seconds % 60

    return f"{minutes}:{seconds:02d}"


# =========================================================
# QIDIRUV NATIJALARINI TUGMA QILISH
# =========================================================

def make_results_keyboard(results):
    builder = InlineKeyboardBuilder()

    for index, item in enumerate(results):
        title = item["title"]

        if len(title) > 45:
            title = title[:42] + "..."

        duration = format_duration(item.get("duration"))

        if duration:
            text = f"🎵 {index + 1}. {title} [{duration}]"
        else:
            text = f"🎵 {index + 1}. {title}"

        builder.row(
            InlineKeyboardButton(
                text=text,
                callback_data=f"download:{item['id']}",
            )
        )

    return builder.as_markup()


# =========================================================
# START
# =========================================================

@dp.message(CommandStart())
async def start_handler(message: Message):

    text = (
        "🎵 <b>YouTube MP3 bot</b>\n\n"
        "Qo'shiq nomi yoki ijrochini yozing.\n\n"
        "Masalan:\n"
        "<code>Yulduz Usmanova Senga</code>\n"
        "<code>Konsta Million</code>\n"
        "<code>Imagine Dragons Believer</code>"
    )

    await message.answer(
        text,
        parse_mode="HTML",
    )


# =========================================================
# QIDIRUV
# =========================================================

@dp.message(F.text)
async def search_handler(message: Message):

    query = message.text.strip()

    if not query:
        return

    if query.startswith("/"):
        return

    status = await message.answer(
        "🔎 YouTube'dan qidiryapman..."
    )

    try:
        results = await asyncio.to_thread(
            search_youtube,
            query,
            MAX_RESULTS,
        )

        if not results:
            await status.edit_text(
                "❌ Hech qanday natija topilmadi.\n\n"
                "Boshqa qo'shiq nomi bilan urinib ko'ring."
            )
            return

        text = (
            f"🎵 <b>{query}</b>\n\n"
            "Kerakli qo'shiqni tanlang:"
        )

        keyboard = make_results_keyboard(results)

        await status.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(e)

        await status.edit_text(
            "❌ Qidirishda xatolik yuz berdi."
        )


# =========================================================
# MP3 YUKLASH
# =========================================================

def download_mp3(video_id: str):
    """
    YouTube videosini MP3 formatga o'tkazadi.
    FFmpeg tizimdan ishlatiladi.
    """

    url = f"https://www.youtube.com/watch?v={video_id}"

    output_template = str(
        DOWNLOAD_DIR / "%(id)s.%(ext)s"
    )

    ydl_opts = {
        "format": "bestaudio/best",

        "outtmpl": output_template,

        "noplaylist": True,

        "quiet": True,

        "no_warnings": True,

        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],

        "postprocessor_args": [
            "-id3v2_version",
            "3",
        ],

        "prefer_ffmpeg": True,

        "writethumbnail": False,

        "addmetadata": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:

        info = ydl.extract_info(
            url,
            download=True,
        )

        title = info.get("title") or "audio"

        filename = DOWNLOAD_DIR / f"{video_id}.mp3"

        if not filename.exists():
            # Ba'zi holatlarda yt-dlp fayl nomini boshqacha yaratishi mumkin
            possible_files = list(
                DOWNLOAD_DIR.glob(f"{video_id}.*")
            )

            mp3_files = [
                x for x in possible_files
                if x.suffix.lower() == ".mp3"
            ]

            if mp3_files:
                filename = mp3_files[0]

        return filename, title


# =========================================================
# MP3 TUGMASI BOSILGANDA
# =========================================================

@dp.callback_query(F.data.startswith("download:"))
async def download_handler(callback: CallbackQuery):

    video_id = callback.data.split(
        "download:",
        1
    )[1]

    await callback.answer(
        "MP3 tayyorlanmoqda..."
    )

    status_message = None

    try:

        status_message = await callback.message.answer(
            "⏳ <b>MP3 tayyorlanmoqda...</b>\n\n"
            "Biroz kuting.",
            parse_mode="HTML",
        )

        # Eski faylni tekshirish
        existing_file = DOWNLOAD_DIR / f"{video_id}.mp3"

        if existing_file.exists():

            filename = existing_file

            title = video_id

        else:

            filename, title = await asyncio.to_thread(
                download_mp3,
                video_id,
            )

        # Fayl mavjudligini tekshirish
        if not filename.exists():

            await status_message.edit_text(
                "❌ MP3 fayli yaratilmadi."
            )

            return

        # Fayl hajmi
        file_size_mb = (
            filename.stat().st_size /
            1024 /
            1024
        )

        logger.info(
            "MP3: %s | %.2f MB",
            filename,
            file_size_mb,
        )

        # Telegram limitini tekshirish
        if file_size_mb > MAX_FILE_SIZE_MB:

            await status_message.edit_text(
                "❌ Fayl juda katta.\n\n"
                f"Fayl hajmi: {file_size_mb:.1f} MB\n"
                f"Limit: {MAX_FILE_SIZE_MB} MB"
            )

            try:
                filename.unlink()
            except Exception:
                pass

            return

        # Faylni Telegramga yuborish
        from aiogram.types import FSInputFile

        audio_file = FSInputFile(
            filename,
            filename=f"{clean_filename(title)}.mp3",
        )

        await callback.message.answer_audio(
            audio=audio_file,
            title=title[:64],
            performer="YouTube",
        )

        await status_message.delete()

        # Yuklangan faylni o'chirish
        try:
            filename.unlink()
        except Exception as e:
            logger.warning(
                "Faylni o'chirishda xato: %s",
                e,
            )

    except Exception as e:

        logger.exception(
            "MP3 yuklash xatosi: %s",
            e,
        )

        if status_message:

            try:
                await status_message.edit_text(
                    "❌ MP3 yuklashda xatolik yuz berdi.\n\n"
                    "Boshqa qo'shiqni sinab ko'ring."
                )
            except Exception:
                pass


# =========================================================
# BOTNI ISHGA TUSHIRISH
# =========================================================

async def main():

    # FFmpeg mavjudligini tekshirish
    ffmpeg_path = shutil.which("ffmpeg")

    if ffmpeg_path:
        logger.info(
            "FFmpeg topildi: %s",
            ffmpeg_path,
        )
    else:
        logger.error(
            "FFmpeg topilmadi! "
            "MP3 konvertatsiya ishlamaydi."
        )

    if not BOT_TOKEN or BOT_TOKEN == "BU_YERGA_BOT_TOKENINGIZNI_YOZING":
        raise RuntimeError(
            "BOT_TOKEN kiritilmagan!"
        )

    logger.info("Bot ishga tushmoqda...")

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("Bot to'xtatildi.")
