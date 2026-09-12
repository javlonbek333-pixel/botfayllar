import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)


# =========================================================
# SOZLAMALAR
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE_MB = 49


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =========================================================
# BOT
# =========================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =========================================================
# QO'LLAB-QUVVATLANADIGAN SAYTLAR
# =========================================================

SUPPORTED_DOMAINS = [
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",

    "instagram.com",

    "tiktok.com",

    "facebook.com",
    "fb.watch",

    "pinterest.com",
    "pin.it",

    "snapchat.com",

    "likee.video",
    "likee.com",

    "threads.net",
]


# =========================================================
# URL TEKSHIRISH
# =========================================================

def is_supported_url(url: str) -> bool:

    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False

        host = parsed.netloc.lower()

        if host.startswith("www."):
            host = host[4:]

        for domain in SUPPORTED_DOMAINS:
            if host == domain or host.endswith("." + domain):
                return True

        return False

    except Exception:
        return False


# =========================================================
# YOUTUBE URLMI?
# =========================================================

def is_youtube_url(url: str) -> bool:

    try:
        host = urlparse(url).netloc.lower()

        return (
            "youtube.com" in host
            or "youtu.be" in host
        )

    except Exception:
        return False


# =========================================================
# URLNI MATNDAN TOPISH
# =========================================================

def extract_url(text: str):

    pattern = r"https?://[^\s]+"

    match = re.search(pattern, text)

    if not match:
        return None

    url = match.group(0)

    # Oxiridagi belgilarni olib tashlash
    url = url.rstrip(".,!?)]}>\"'")

    return url


# =========================================================
# FAYL NOMINI TOZALASH
# =========================================================

def clean_filename(name: str) -> str:

    name = re.sub(
        r'[\\/*?:"<>|]',
        "",
        name
    )

    name = name.strip()

    if not name:
        name = "download"

    return name[:100]


# =========================================================
# QIDIRUV
# =========================================================

def search_youtube(query: str):

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }

    try:

        with yt_dlp.YoutubeDL(options) as ydl:

            info = ydl.extract_info(
                f"ytsearch10:{query}",
                download=False
            )

        results = []

        for item in info.get("entries", []):

            if not item:
                continue

            video_id = item.get("id")

            if not video_id:
                continue

            results.append({
                "id": video_id,
                "title": item.get(
                    "title",
                    "Noma'lum"
                ),
                "duration": item.get(
                    "duration"
                ),
            })

        return results

    except Exception as e:

        logger.exception(
            "YouTube search error: %s",
            e
        )

        return []


# =========================================================
# QIDIRUV TUGMALARI
# =========================================================

def search_keyboard(results):

    buttons = []

    for index, item in enumerate(results):

        title = item["title"]

        if len(title) > 42:
            title = title[:42] + "..."

        duration = item.get("duration")

        if duration:

            try:

                minutes = int(duration) // 60
                seconds = int(duration) % 60

                time_text = (
                    f" [{minutes}:{seconds:02d}]"
                )

            except Exception:

                time_text = ""

        else:

            time_text = ""

        buttons.append([
            InlineKeyboardButton(
                text=f"🎵 {index + 1}. {title}{time_text}",
                callback_data=f"yt:{item['id']}"
            )
        ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# =========================================================
# YOUTUBE TANLASH
# =========================================================

def youtube_format_keyboard(video_id):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 Video",
                    callback_data=f"ytvideo:{video_id}"
                ),
                InlineKeyboardButton(
                    text="🎵 MP3",
                    callback_data=f"ytaudio:{video_id}"
                )
            ]
        ]
    )


# =========================================================
# DOWNLOAD FUNKSIYASI
# =========================================================

def download_media(
    url: str,
    mode: str = "video"
):

    unique_name = "%(id)s"

    output = str(
        DOWNLOAD_DIR / unique_name
    )

    # -----------------------------------------------------
    # AUDIO
    # -----------------------------------------------------

    if mode == "audio":

        options = {

            "format": "bestaudio/best",

            "outtmpl": output + ".%(ext)s",

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
        }

    # -----------------------------------------------------
    # VIDEO
    # -----------------------------------------------------

    else:

        options = {

            # 720p gacha olish
            "format":
                "bestvideo[height<=720]+"
                "bestaudio/"
                "best[height<=720]/best",

            "outtmpl": output + ".%(ext)s",

            "merge_output_format": "mp4",

            "noplaylist": True,

            "quiet": True,

            "no_warnings": True,

        }

    # -----------------------------------------------------
    # DOWNLOAD
    # -----------------------------------------------------

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        title = info.get(
            "title",
            "download"
        )

        video_id = info.get(
            "id"
        )

    # -----------------------------------------------------
    # FAYLNI TOPISH
    # -----------------------------------------------------

    files = list(
        DOWNLOAD_DIR.glob(
            f"{video_id}.*"
        )
    )

    # vaqtinchalik fayllarni chiqarib tashlash
    files = [
        f for f in files
        if not f.name.endswith(".part")
        and not f.name.endswith(".ytdl")
    ]

    if not files:

        raise FileNotFoundError(
            "Yuklangan fayl topilmadi"
        )

    # MP3 bo'lsa
    if mode == "audio":

        mp3_files = [
            f for f in files
            if f.suffix.lower() == ".mp3"
        ]

        if mp3_files:
            file_path = mp3_files[0]

        else:
            file_path = files[0]

    else:

        # MP4 ni afzal ko'ramiz
        mp4_files = [
            f for f in files
            if f.suffix.lower() == ".mp4"
        ]

        if mp4_files:
            file_path = mp4_files[0]

        else:
            file_path = files[0]

    return file_path, title


# =========================================================
# START
# =========================================================

@dp.message(CommandStart())
async def start_handler(message: Message):

    text = (
        "👋 <b>Universal Downloader Bot</b>\n\n"

        "Quyidagilardan yuklab olishingiz mumkin:\n\n"

        "• Instagram — post, stories, reels\n"
        "• YouTube — video, Shorts, audio\n"
        "• TikTok — video\n"
        "• Facebook — reels, video\n"
        "• Pinterest — rasm, video\n"
        "• Snapchat — rasm, video\n"
        "• Likee — rasm, video\n"
        "• Threads — rasm, video\n\n"

        "🔗 Havolani yuboring."
    )

    await message.answer(
        text,
        parse_mode="HTML"
    )


# =========================================================
# LINK YUBORILGANDA
# =========================================================

@dp.message(F.text)
async def message_handler(message: Message):

    text = message.text.strip()

    # URL topish
    url = extract_url(text)

    # -----------------------------------------------------
    # AGAR URL BO'LSA
    # -----------------------------------------------------

    if url:

        if not is_supported_url(url):

            await message.answer(
                "❌ Bu sayt hozircha qo'llab-quvvatlanmaydi.\n\n"

                "Qo'llab-quvvatlanadigan saytlar:\n"
                "Instagram\n"
                "YouTube\n"
                "TikTok\n"
                "Facebook\n"
                "Pinterest\n"
                "Snapchat\n"
                "Likee\n"
                "Threads"
            )

            return

        # YouTube uchun Video / MP3 tanlash
        if is_youtube_url(url):

            await message.answer(
                "🎬 <b>YouTube</b>\n\n"
                "Qaysi format kerak?",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🎬 Video",
                                callback_data=f"urlvideo:{url}"
                            ),
                            InlineKeyboardButton(
                                text="🎵 MP3",
                                callback_data=f"urlaudio:{url}"
                            )
                        ]
                    ]
                ),
                parse_mode="HTML"
            )

            return

        # Boshqa saytlar avtomatik video/rasm
        await download_and_send(
            message,
            url,
            "video"
        )

        return

    # -----------------------------------------------------
    # URL EMAS -> YOUTUBE QIDIRUV
    # -----------------------------------------------------

    status = await message.answer(
        "🔎 YouTube'dan qidiryapman..."
    )

    results = await asyncio.to_thread(
        search_youtube,
        text
    )

    if not results:

        await status.edit_text(
            "❌ Hech qanday natija topilmadi."
        )

        return

    await status.edit_text(
        "🎵 <b>Natijalar:</b>\n\n"
        "Kerakli qo'shiqni tanlang:",
        reply_markup=search_keyboard(results),
        parse_mode="HTML"
    )


# =========================================================
# QIDIRUV NATIJASIDAN TANLASH
# =========================================================

@dp.callback_query(F.data.startswith("yt:"))
async def youtube_selected(
    callback: CallbackQuery
):

    video_id = callback.data.split(
        "yt:",
        1
    )[1]

    await callback.answer()

    await callback.message.answer(
        "Qaysi format kerak?",
        reply_markup=youtube_format_keyboard(
            video_id
        )
    )


# =========================================================
# QIDIRUV -> VIDEO
# =========================================================

@dp.callback_query(
    F.data.startswith("ytvideo:")
)
async def youtube_video_callback(
    callback: CallbackQuery
):

    video_id = callback.data.split(
        "ytvideo:",
        1
    )[1]

    await callback.answer(
        "Video yuklanmoqda..."
    )

    url = (
        f"https://www.youtube.com/watch?v={video_id}"
    )

    await download_and_send(
        callback.message,
        url,
        "video"
    )


# =========================================================
# QIDIRUV -> AUDIO
# =========================================================

@dp.callback_query(
    F.data.startswith("ytaudio:")
)
async def youtube_audio_callback(
    callback: CallbackQuery
):

    video_id = callback.data.split(
        "ytaudio:",
        1
    )[1]

    await callback.answer(
        "MP3 tayyorlanmoqda..."
    )

    url = (
        f"https://www.youtube.com/watch?v={video_id}"
    )

    await download_and_send(
        callback.message,
        url,
        "audio"
    )


# =========================================================
# URL -> VIDEO
# =========================================================

@dp.callback_query(
    F.data.startswith("urlvideo:")
)
async def url_video_callback(
    callback: CallbackQuery
):

    url = callback.data.split(
        "urlvideo:",
        1
    )[1]

    await callback.answer(
        "Video yuklanmoqda..."
    )

    await download_and_send(
        callback.message,
        url,
        "video"
    )


# =========================================================
# URL -> AUDIO
# =========================================================

@dp.callback_query(
    F.data.startswith("urlaudio:")
)
async def url_audio_callback(
    callback: CallbackQuery
):

    url = callback.data.split(
        "urlaudio:",
        1
    )[1]

    await callback.answer(
        "MP3 tayyorlanmoqda..."
    )

    await download_and_send(
        callback.message,
        url,
        "audio"
    )


# =========================================================
# YUKLASH VA TELEGRAMGA YUBORISH
# =========================================================

async def download_and_send(
    message: Message,
    url: str,
    mode: str
):

    status = await message.answer(
        "⏳ <b>Yuklanmoqda...</b>\n\n"
        "Biroz kuting.",
        parse_mode="HTML"
    )

    file_path = None

    try:

        file_path, title = await asyncio.to_thread(
            download_media,
            url,
            mode
        )

        # -------------------------------------------------
        # HAJMI
        # -------------------------------------------------

        size_mb = (
            file_path.stat().st_size
            / 1024
            / 1024
        )

        logger.info(
            "Downloaded: %s | %.2f MB",
            file_path,
            size_mb
        )

        if size_mb > MAX_FILE_SIZE_MB:

            await status.edit_text(
                "❌ Fayl juda katta.\n\n"
                f"📦 Hajmi: {size_mb:.1f} MB\n"
                f"📏 Limit: {MAX_FILE_SIZE_MB} MB"
            )

            return

        # -------------------------------------------------
        # AUDIO
        # -------------------------------------------------

        if mode == "audio":

            audio = FSInputFile(
                file_path,
                filename=(
                    clean_filename(title)
                    + ".mp3"
                )
            )

            await message.answer_audio(
                audio=audio,
                title=title[:64]
            )

        # -------------------------------------------------
        # VIDEO
        # -------------------------------------------------

        else:

            video = FSInputFile(
                file_path,
                filename=(
                    clean_filename(title)
                    + file_path.suffix
                )
            )

            await message.answer_video(
                video=video,
                caption=title[:1024]
            )

        await status.delete()

    except Exception as e:

        logger.exception(
            "Download error: %s",
            e
        )

        try:

            await status.edit_text(
                "❌ Yuklab bo'lmadi.\n\n"
                "Havola ishlamasligi, video "
                "o'chirilgan yoki sayt vaqtincha "
                "cheklov qo'ygan bo'lishi mumkin."
            )

        except Exception:
            pass

    finally:

        # -------------------------------------------------
        # FAYLNI O'CHIRISH
        # -------------------------------------------------

        if file_path and file_path.exists():

            try:
                file_path.unlink()

            except Exception as e:

                logger.warning(
                    "Fayl o'chirilmadi: %s",
                    e
                )


# =========================================================
# START BOT
# =========================================================

async def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN Railway Variables'da "
            "kiritilmagan!"
        )

    ffmpeg = shutil.which("ffmpeg")

    if not ffmpeg:

        logger.error(
            "FFmpeg topilmadi!"
        )

    else:

        logger.info(
            "FFmpeg: %s",
            ffmpeg
        )

    logger.info(
        "Universal downloader bot ishga tushdi."
    )

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types()
    )


if __name__ == "__main__":

    asyncio.run(main())
