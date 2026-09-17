import os
import re
import asyncio
import logging
import subprocess
from pathlib import Path

import requests
import yt_dlp

from shazamio import Shazam

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

DOWNLOAD_DIR = Path("/tmp/bot_downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Telegramga yuborishdan oldingi maqsadli hajm
TARGET_SIZE_MB = 14
TARGET_SIZE = TARGET_SIZE_MB * 1024 * 1024

MAX_DOWNLOAD_MB = 500
MAX_DOWNLOAD_SIZE = MAX_DOWNLOAD_MB * 1024 * 1024

COOKIES_FILE = "/app/cookies.txt"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# URL TEKSHIRISH
# =========================================================

def is_url(text: str) -> bool:
    if not text:
        return False

    return bool(
        re.match(
            r"^https?://",
            text.strip(),
            re.IGNORECASE
        )
    )


# =========================================================
# COOKIES
# =========================================================

def get_cookie_options():
    """
    Agar Railway /app/cookies.txt mavjud bo'lsa,
    yt-dlp undan foydalanadi.

    Fayl bo'lmasa, cookies ishlatilmaydi.
    """

    if os.path.exists(COOKIES_FILE):
        logger.info("cookies.txt topildi")
        return {
            "cookiefile": COOKIES_FILE
        }

    logger.info("cookies.txt topilmadi. Cookiesiz ishlayapti.")
    return {}


# =========================================================
# FFPROBE
# =========================================================

def get_video_duration(file_path: str) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        return float(result.stdout.strip())

    except Exception:
        return 0


# =========================================================
# VIDEO SIQISH
# =========================================================

def compress_video(input_file: str, output_file: str) -> str:
    """
    Videoni 14 MB atrofida yoki undan kichik qilishga harakat qiladi.

    Bir necha bitrate bilan qayta urinadi.
    """

    original_size = os.path.getsize(input_file)

    if original_size <= TARGET_SIZE:
        return input_file

    duration = get_video_duration(input_file)

    if duration <= 0:
        duration = 60

    # Bir necha urinish
    bitrates = [
        900,
        700,
        550,
        450,
        350,
        280,
        220,
    ]

    for video_kbps in bitrates:

        # Audio
        audio_kbps = 48

        total_kbps = video_kbps + audio_kbps

        target_kbps = int(
            (TARGET_SIZE * 8 / duration) / 1000
        )

        # Juda katta bo'lib ketmasin
        final_video_kbps = min(
            video_kbps,
            max(150, target_kbps - audio_kbps)
        )

        logger.info(
            f"Compression: video={final_video_kbps}k"
        )

        temp_output = (
            str(DOWNLOAD_DIR / f"compressed_{os.getpid()}.mp4")
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_file,

            "-vf",
            "scale='min(1280,iw)':'-2'",

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-b:v",
            f"{final_video_kbps}k",

            "-maxrate",
            f"{final_video_kbps}k",

            "-bufsize",
            f"{final_video_kbps * 2}k",

            "-c:a",
            "aac",

            "-b:a",
            f"{audio_kbps}k",

            "-movflags",
            "+faststart",

            temp_output,
        ]

        try:
            subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=600,
                check=True,
            )

            if os.path.exists(temp_output):

                size = os.path.getsize(temp_output)

                logger.info(
                    f"Compressed size: {size / 1024 / 1024:.2f} MB"
                )

                if size <= TARGET_SIZE:
                    return temp_output

                try:
                    os.remove(temp_output)
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"FFmpeg compression error: {e}")

    # Oxirgi variant
    return input_file


# =========================================================
# YT-DLP VIDEO DOWNLOAD
# =========================================================

def download_video(url: str, work_dir: Path):

    output_template = str(
        work_dir / "%(title).80s_%(id)s.%(ext)s"
    )

    opts = {
        # MUHIM:
        # Avval video+audio,
        # bo'lmasa bitta tayyor format.
        "format": "bestvideo*+bestaudio/best",

        "outtmpl": output_template,

        "merge_output_format": "mp4",

        "noplaylist": True,

        "quiet": True,

        "no_warnings": False,

        "retries": 3,

        "fragment_retries": 3,

        "socket_timeout": 30,

        "concurrent_fragment_downloads": 4,

        "postprocessors": [],
    }

    # Cookies mavjud bo'lsa qo'shamiz
    opts.update(get_cookie_options())

    logger.info(f"Downloading URL: {url}")

    with yt_dlp.YoutubeDL(opts) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        downloaded_file = ydl.prepare_filename(info)

        # Merge'dan keyin mp4 bo'lishi mumkin
        possible_files = [
            downloaded_file,
            os.path.splitext(downloaded_file)[0] + ".mp4",
            os.path.splitext(downloaded_file)[0] + ".mkv",
            os.path.splitext(downloaded_file)[0] + ".webm",
        ]

        for file in possible_files:
            if os.path.exists(file):
                return file, info

        # Papkadan qidiramiz
        files = list(work_dir.glob("*"))

        if files:
            return str(files[0]), info

        raise FileNotFoundError(
            "Yuklangan fayl topilmadi"
        )


# =========================================================
# AUDIO DOWNLOAD
# =========================================================

def download_audio(search_text: str, work_dir: Path):

    output_template = str(
        work_dir / "%(title).80s_%(id)s.%(ext)s"
    )

    opts = {
        "format": "bestaudio/best",

        "outtmpl": output_template,

        "noplaylist": True,

        "quiet": True,

        "no_warnings": False,

        "retries": 3,

        "fragment_retries": 3,

        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    opts.update(get_cookie_options())

    query = f"ytsearch1:{search_text}"

    logger.info(
        f"Audio search: {search_text}"
    )

    with yt_dlp.YoutubeDL(opts) as ydl:

        info = ydl.extract_info(
            query,
            download=True
        )

        entries = info.get("entries")

        if not entries:
            raise Exception(
                "Qo'shiq topilmadi"
            )

        video_info = entries[0]

        original = ydl.prepare_filename(
            video_info
        )

        mp3_file = (
            os.path.splitext(original)[0]
            + ".mp3"
        )

        if os.path.exists(mp3_file):
            return mp3_file, video_info

        # fallback
        for file in work_dir.glob("*.mp3"):
            return str(file), video_info

        raise FileNotFoundError(
            "MP3 fayl topilmadi"
        )


# =========================================================
# DEEZER SEARCH
# =========================================================

def search_deezer(query: str):

    try:

        response = requests.get(
            "https://api.deezer.com/search",
            params={
                "q": query,
                "limit": 10,
            },
            timeout=15,
        )

        if response.status_code != 200:
            return []

        data = response.json()

        return data.get("data", [])

    except Exception as e:

        logger.error(
            f"Deezer error: {e}"
        )

        return []


# =========================================================
# SHAZAM
# =========================================================

async def recognize_audio(file_path: str):

    try:

        shazam = Shazam()

        result = await shazam.recognize_song(
            file_path
        )

        track = result.get("track")

        if not track:
            return None

        title = track.get(
            "title",
            ""
        )

        artist = track.get(
            "subtitle",
            ""
        )

        if title and artist:
            return f"{artist} - {title}"

        if title:
            return title

        return None

    except Exception as e:

        logger.error(
            f"Shazam error: {e}"
        )

        return None


# =========================================================
# /START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
👋 Assalomu aleykum

📥 MEDIA YUKLASH

• Instagram — post, stories, reels
• YouTube — video, shorts, audio
• TikTok — suv belgisiz video
• Facebook — reels
• Pinterest — rasm, video
• Snapchat — rasm, video
• Likee — rasm, video
• Threads — rasm, video
• OK.ru — video

🎵 MUSIQA

• Qo'shiq nomini yozing
• Xonanda nomini yozing
• Qo'shiqni qidirib MP3 qilib olishingiz mumkin

🎧 ANIQLASH

Audio, video yoki voice yuboring —
qo'shiqni aniqlashga harakat qilaman.

🚀 Boshlash uchun havolani yoki
qo'shiq nomini yuboring.
"""

    await update.message.reply_text(
        text
    )


# =========================================================
# URL HANDLER
# =========================================================

async def handle_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    url = update.message.text.strip()

    msg = await update.message.reply_text(
        "⏳ Yuklanmoqda..."
    )

    work_dir = (
        DOWNLOAD_DIR
        / str(update.effective_user.id)
    )

    work_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        file_path, info = await asyncio.to_thread(
            download_video,
            url,
            work_dir
        )

        if not os.path.exists(file_path):
            raise Exception(
                "Fayl topilmadi"
            )

        size = os.path.getsize(
            file_path
        )

        if size > MAX_DOWNLOAD_SIZE:

            await msg.edit_text(
                "⚠️ Fayl juda katta.\n"
                "Siqishga harakat qilaman..."
            )

        # 14 MB dan katta bo'lsa siqamiz
        if size > TARGET_SIZE:

            compressed = await asyncio.to_thread(
                compress_video,
                file_path,
                str(
                    work_dir
                    / "final_compressed.mp4"
                )
            )

            if compressed != file_path:

                file_path = compressed

        final_size = os.path.getsize(
            file_path
        )

        await msg.edit_text(
            f"📤 Yuborilmoqda...\n"
            f"📦 Hajmi: {final_size / 1024 / 1024:.2f} MB"
        )

        with open(
            file_path,
            "rb"
        ) as video:

            await update.message.reply_video(
                video=video,
                supports_streaming=True,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
            )

        await msg.delete()

    except Exception as e:

        logger.exception(
            "Download error"
        )

        error_text = str(e)

        if len(error_text) > 1500:
            error_text = error_text[-1500:]

        await msg.edit_text(
            "❌ Yuklashda xatolik:\n\n"
            f"`{error_text}`",
            parse_mode="Markdown",
        )

    finally:

        # Fayllarni tozalash
        try:

            for file in work_dir.glob("*"):

                try:
                    file.unlink()
                except Exception:
                    pass

        except Exception:
            pass


# =========================================================
# MUSIC SEARCH
# =========================================================

async def handle_music_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.message.text.strip()

    msg = await update.message.reply_text(
        "🔎 Qo'shiq qidirilmoqda..."
    )

    results = await asyncio.to_thread(
        search_deezer,
        query
    )

    if not results:

        await msg.edit_text(
            "🔎 Deezer'dan natija chiqmagan.\n"
            "YouTube orqali qidirib ko'raman..."
        )

        work_dir = (
            DOWNLOAD_DIR
            / str(update.effective_user.id)
        )

        work_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        try:

            file_path, info = await asyncio.to_thread(
                download_audio,
                query,
                work_dir
            )

            with open(
                file_path,
                "rb"
            ) as audio:

                await update.message.reply_audio(
                    audio=audio,
                    title=info.get(
                        "title",
                        query
                    ),
                    performer=info.get(
                        "uploader",
                        ""
                    ),
                )

            await msg.delete()

        except Exception as e:

            await msg.edit_text(
                "❌ Qo'shiq topilmadi.\n\n"
                f"`{str(e)[:1000]}`",
                parse_mode="Markdown",
            )

        return

    keyboard = []

    for i, track in enumerate(
        results[:10]
    ):

        artist = track.get(
            "artist",
            {}
        ).get(
            "name",
            ""
        )

        title = track.get(
            "title",
            ""
        )

        label = f"{i + 1}. {artist} — {title}"

        callback = (
            f"song|{artist}|{title}"
        )

        # Telegram callback 64 byte limit
        callback = callback[:60]

        keyboard.append(
            [
                InlineKeyboardButton(
                    label[:60],
                    callback_data=callback
                )
            ]
        )

    await msg.edit_text(
        "🎵 Natijalar:\n\n"
        "Kerakli qo'shiqni tanlang:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# SONG CALLBACK
# =========================================================

async def song_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    if not data.startswith(
        "song|"
    ):
        return

    parts = data.split(
        "|",
        2
    )

    if len(parts) != 3:
        return

    artist = parts[1]
    title = parts[2]

    search_text = (
        f"{artist} {title}"
    )

    await query.edit_message_text(
        "⏳ MP3 tayyorlanmoqda..."
    )

    work_dir = (
        DOWNLOAD_DIR
        / str(query.from_user.id)
    )

    work_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        # Deezer URL'ni yuklamaymiz.
        # Haqiqiy audio manbani YouTube'dan qidiramiz.
        file_path, info = await asyncio.to_thread(
            download_audio,
            search_text,
            work_dir
        )

        with open(
            file_path,
            "rb"
        ) as audio:

            await context.bot.send_audio(
                chat_id=query.message.chat_id,
                audio=audio,
                title=title,
                performer=artist,
            )

        await query.message.delete()

    except Exception as e:

        logger.exception(
            "Song download error"
        )

        await query.edit_message_text(
            "❌ MP3 yuklashda xatolik:\n\n"
            f"`{str(e)[:1200]}`",
            parse_mode="Markdown",
        )

    finally:

        try:

            for file in work_dir.glob("*"):

                try:
                    file.unlink()
                except Exception:
                    pass

        except Exception:
            pass


# =========================================================
# SHAZAM HANDLER
# =========================================================

async def handle_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.message

    file = None
    suffix = ".mp3"

    if message.audio:

        file = await message.audio.get_file()

        suffix = ".mp3"

    elif message.voice:

        file = await message.voice.get_file()

        suffix = ".ogg"

    elif message.video:

        file = await message.video.get_file()

        suffix = ".mp4"

    elif message.video_note:

        file = await message.video_note.get_file()

        suffix = ".mp4"

    else:
        return

    work_dir = (
        DOWNLOAD_DIR
        / str(update.effective_user.id)
    )

    work_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    input_file = (
        work_dir
        / f"recognize{suffix}"
    )

    msg = await message.reply_text(
        "🎧 Qo'shiq aniqlanmoqda..."
    )

    try:

        await file.download_to_drive(
            input_file
        )

        result = await recognize_audio(
            str(input_file)
        )

        if result:

            await msg.edit_text(
                "🎵 Aniqlangan qo'shiq:\n\n"
                f"**{result}**",
                parse_mode="Markdown",
            )

            # Aniqlangan qo'shiqni qidirish
            keyboard = [
                [
                    InlineKeyboardButton(
                        "⬇️ MP3 yuklash",
                        callback_data=(
                            f"song|"
                            f"{result[:50]}"
                        )[:60]
                    )
                ]
            ]

        else:

            await msg.edit_text(
                "❌ Qo'shiqni aniqlay olmadim."
            )

    except Exception as e:

        logger.exception(
            "Recognition error"
        )

        await msg.edit_text(
            "❌ Aniqlashda xatolik:\n"
            f"`{str(e)[:1000]}`",
            parse_mode="Markdown",
        )

    finally:

        try:
            if input_file.exists():
                input_file.unlink()
        except Exception:
            pass


# =========================================================
# TEXT HANDLER
# =========================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text.strip()

    if is_url(text):

        await handle_url(
            update,
            context
        )

    else:

        await handle_music_search(
            update,
            context
        )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.exception(
        "Unhandled exception",
        exc_info=context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN Railway Variables'da topilmadi!"
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            song_callback,
            pattern=r"^song\|"
        )
    )

    app.add_handler(
        MessageHandler(
            filters.AUDIO
            | filters.VOICE
            | filters.VIDEO
            | filters.VIDEO_NOTE,
            handle_audio
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_text
        )
    )

    app.add_error_handler(
        error_handler
    )

    logger.info(
        "BOT ISHLAYAPTI"
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
