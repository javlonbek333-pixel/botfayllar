import os
import re
import base64
import asyncio
import logging
import tempfile
import shutil
import subprocess
from pathlib import Path

import requests
import yt_dlp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from shazamio import Shazam


# ============================================================
# SOZLAMALAR
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Telegramga yuboriladigan maksimal video
TARGET_SIZE = 14 * 1024 * 1024  # 14 MiB

# Manbadan olinadigan maksimal fayl
MAX_SOURCE_SIZE = 1024 * 1024 * 1024  # 1 GB

VIDEO_HEIGHT = 480

DOWNLOAD_DIR = Path("/tmp/bot_downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Google Custom Search ixtiyoriy
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# YOUTUBE COOKIES
# ============================================================

YOUTUBE_COOKIE_FILE = DOWNLOAD_DIR / "youtube_cookies.txt"


def prepare_youtube_cookies():
    """
    Railway Variables:

    YOUTUBE_COOKIES_B64
    yoki
    YOUTUBE_COOKIES
    """

    b64 = os.getenv("YOUTUBE_COOKIES_B64")
    raw = os.getenv("YOUTUBE_COOKIES")

    try:
        if b64:
            data = base64.b64decode(b64).decode("utf-8")

            YOUTUBE_COOKIE_FILE.write_text(
                data,
                encoding="utf-8"
            )

            logger.info("YouTube cookies yuklandi.")

            return str(YOUTUBE_COOKIE_FILE)

        if raw:
            YOUTUBE_COOKIE_FILE.write_text(
                raw,
                encoding="utf-8"
            )

            logger.info("YouTube cookies yuklandi.")

            return str(YOUTUBE_COOKIE_FILE)

    except Exception:
        logger.exception(
            "YouTube cookies yozishda xato"
        )

    return None


COOKIE_FILE = prepare_youtube_cookies()


# ============================================================
# URL
# ============================================================

URL_RE = re.compile(
    r"https?://[^\s]+",
    re.IGNORECASE
)


def is_youtube(url):
    url = url.lower()

    return (
        "youtube.com" in url
        or "youtu.be" in url
        or "youtube-nocookie.com" in url
    )


def is_supported_url(url):
    domains = (
        "youtube.com",
        "youtu.be",
        "instagram.com",
        "tiktok.com",
        "facebook.com",
        "fb.watch",
        "ok.ru",
        "odnoklassniki.ru",
        "pinterest.com",
        "snapchat.com",
        "likee.video",
        "threads.net",
    )

    url = url.lower()

    return any(
        domain in url
        for domain in domains
    )


# ============================================================
# YT-DLP OPTIONS
# ============================================================

def base_ydl_options(url=None):

    options = {
        "quiet": True,
        "no_warnings": True,

        "noplaylist": True,

        "retries": 3,
        "fragment_retries": 3,

        "socket_timeout": 30,

        "concurrent_fragment_downloads": 8,

        "max_filesize": MAX_SOURCE_SIZE,

        "nocheckcertificate": True,

        "ffmpeg_location": "ffmpeg",
    }

    # Cookie faqat YouTube uchun
    if (
        url
        and is_youtube(url)
        and COOKIE_FILE
    ):
        options["cookiefile"] = COOKIE_FILE

    return options


# ============================================================
# VIDEO DOWNLOAD
# ============================================================

def download_video_sync(
    url: str,
    output_dir: Path
):

    output_template = str(
        output_dir / "%(title).80s.%(ext)s"
    )

    options = base_ydl_options(url)

    options.update({

        "format": (
            "bestvideo[height<=480]+bestaudio/"
            "best[height<=480]/"
            "bestvideo+bestaudio/best"
        ),

        "merge_output_format": "mp4",

        "outtmpl": output_template,

    })

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        files = list(
            output_dir.glob("*")
        )

        video_files = [
            f
            for f in files
            if (
                f.is_file()
                and f.suffix.lower()
                in (
                    ".mp4",
                    ".mkv",
                    ".webm",
                    ".mov",
                )
            )
        ]

        if not video_files:
            raise RuntimeError(
                "Video fayl topilmadi."
            )

        video_files.sort(
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        return video_files[0], info


# ============================================================
# AUDIO DOWNLOAD
# ============================================================

def download_audio_sync(
    url: str,
    output_dir: Path
):

    output_template = str(
        output_dir / "%(title).80s.%(ext)s"
    )

    options = base_ydl_options(url)

    options.update({

        # Faqat audio
        "format": "bestaudio/best",

        "outtmpl": output_template,

        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ],

    })

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        mp3_files = list(
            output_dir.glob("*.mp3")
        )

        if not mp3_files:
            raise RuntimeError(
                "MP3 fayl topilmadi."
            )

        mp3_files.sort(
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        return mp3_files[0], info


# ============================================================
# VIDEO DAVOMIYLIGI
# ============================================================

def get_duration(file_path):

    command = [
        "ffprobe",
        "-v",
        "error",

        "-show_entries",
        "format=duration",

        "-of",
        "default=noprint_wrappers=1:nokey=1",

        str(file_path),
    ]

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
        )

        value = result.stdout.strip()

        if value:
            return float(value)

    except Exception:
        pass

    return 60.0


# ============================================================
# VIDEO 480P + 14 MB
# ============================================================

def compress_video_sync(
    input_file: Path,
    output_file: Path
):

    duration = get_duration(
        input_file
    )

    if duration <= 0:
        duration = 60

    # 14 MB ichida qolish uchun
    total_kbps = int(
        (TARGET_SIZE * 8 / duration / 1000)
        * 0.86
    )

    # Audio
    audio_kbps = 64

    # Video bitrate
    video_kbps = max(
        80,
        total_kbps - audio_kbps
    )

    # Haddan tashqari katta bo'lmasin
    video_kbps = min(
        video_kbps,
        1600
    )

    command = [
        "ffmpeg",
        "-y",

        "-i",
        str(input_file),

        "-vf",
        "scale='min(480,iw)':'-2'",

        "-c:v",
        "libx264",

        "-preset",
        "ultrafast",

        "-b:v",
        f"{video_kbps}k",

        "-maxrate",
        f"{video_kbps}k",

        "-bufsize",
        f"{video_kbps * 2}k",

        "-c:a",
        "aac",

        "-b:a",
        f"{audio_kbps}k",

        "-movflags",
        "+faststart",

        str(output_file),
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:

        raise RuntimeError(
            "FFmpeg xatosi:\n"
            + result.stderr[-2000:]
        )

    # Agar 14 MB dan oshgan bo'lsa
    if output_file.exists():

        if output_file.stat().st_size > TARGET_SIZE:

            smaller_file = (
                output_file.parent
                / "final_small.mp4"
            )

            smaller_bitrate = max(
                60,
                int(video_kbps * 0.65)
            )

            command2 = [
                "ffmpeg",
                "-y",

                "-i",
                str(input_file),

                "-vf",
                "scale='min(480,iw)':'-2'",

                "-c:v",
                "libx264",

                "-preset",
                "ultrafast",

                "-b:v",
                f"{smaller_bitrate}k",

                "-c:a",
                "aac",

                "-b:a",
                "48k",

                "-movflags",
                "+faststart",

                str(smaller_file),
            ]

            result2 = subprocess.run(
                command2,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )

            if result2.returncode != 0:

                raise RuntimeError(
                    "Video siqishda xato:\n"
                    + result2.stderr[-2000:]
                )

            if smaller_file.exists():

                output_file.unlink(
                    missing_ok=True
                )

                smaller_file.rename(
                    output_file
                )

    # Oxirgi tekshiruv
    if (
        not output_file.exists()
        or output_file.stat().st_size > TARGET_SIZE
    ):

        raise RuntimeError(
            "Video 14 MB limitiga sig'madi."
        )

    return output_file


# ============================================================
# YOUTUBE QIDIRUV — 50 TA
# ============================================================

def youtube_search_sync(query):

    options = base_ydl_options()

    options.update({
        "extract_flat": True,
    })

    with yt_dlp.YoutubeDL(options) as ydl:

        result = ydl.extract_info(
            f"ytsearch50:{query}",
            download=False
        )

        entries = (
            result.get("entries", [])
            if result
            else []
        )

        results = []

        for item in entries:

            if not item:
                continue

            video_id = item.get("id")

            if not video_id:
                continue

            title = (
                item.get("title")
                or "Noma'lum"
            )

            results.append({
                "title": title,
                "url": (
                    "https://www.youtube.com/watch?v="
                    + video_id
                ),
            })

            if len(results) >= 50:
                break

        return results


# ============================================================
# GOOGLE SEARCH
# ============================================================

def google_search_sync(query):

    if (
        not GOOGLE_API_KEY
        or not GOOGLE_CX
    ):
        return []

    results = []

    for start in (
        1,
        11,
        21,
        31,
        41,
    ):

        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CX,
            "q": query,
            "num": 10,
            "start": start,
        }

        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
            timeout=20,
        )

        response.raise_for_status()

        data = response.json()

        for item in data.get(
            "items",
            []
        ):

            url = item.get(
                "link",
                ""
            )

            title = item.get(
                "title",
                "Noma'lum"
            )

            if url:

                results.append({
                    "title": title,
                    "url": url,
                })

            if len(results) >= 50:
                return results

    return results[:50]


# ============================================================
# SHAZAM
# ============================================================

async def recognize_shazam(
    file_path
):

    shazam = Shazam()

    result = await shazam.recognize(
        file_path
    )

    track = result.get(
        "track",
        {}
    )

    title = track.get(
        "title"
    )

    artist = track.get(
        "subtitle"
    )

    if not title:
        return None

    return {
        "title": title,
        "artist": (
            artist
            or "Noma'lum ijrochi"
        ),
        "query": (
            f"{artist or ''} {title}"
        ).strip(),
    }


# ============================================================
# FOYDALANUVCHI QIDIRUVLARI
# ============================================================

USER_SEARCH_DATA = {}


# ============================================================
# PAGINATION MATNI
# ============================================================

def search_text(
    page,
    total
):

    start = page * 10 + 1

    end = min(
        page * 10 + 10,
        total
    )

    return (
        "🎵 <b>Qo'shiq qidiruvi</b>\n\n"
        f"📊 Jami: <b>{total}</b> ta\n"
        f"📄 Natijalar: <b>{start}-{end}</b>\n\n"
        "👇 Qo'shiqni tanlang:"
    )


# ============================================================
# QIDIRUV KNOPKALARI
# ============================================================

def make_search_keyboard(
    user_id,
    page,
    total
):

    data = USER_SEARCH_DATA.get(
        user_id
    )

    if not data:
        return InlineKeyboardMarkup([])

    results = data.get(
        "results",
        []
    )

    start = page * 10

    end = min(
        start + 10,
        len(results)
    )

    keyboard = []

    for index in range(
        start,
        end
    ):

        title = results[index][
            "title"
        ]

        button_text = (
            f"🎵 {index + 1}. "
            f"{title[:55]}"
        )

        keyboard.append([
            InlineKeyboardButton(
                button_text,
                callback_data=f"song:{index}"
            )
        ])

    navigation = []

    if page > 0:

        navigation.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=f"page:{page - 1}"
            )
        )

    navigation.append(
        InlineKeyboardButton(
            f"{page + 1}/5",
            callback_data="page:current"
        )
    )

    if (
        page < 4
        and end < total
    ):

        navigation.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=f"page:{page + 1}"
            )
        )

    keyboard.append(
        navigation
    )

    return InlineKeyboardMarkup(
        keyboard
    )


# ============================================================
# VIDEO / MP3 PROCESS
# ============================================================

async def process_video(
    update,
    context,
    url,
    audio_only=False
):

    chat_id = update.effective_chat.id

    status = await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ Yuklanmoqda..."
    )

    work_dir = Path(
        tempfile.mkdtemp(
            prefix="media_",
            dir=str(DOWNLOAD_DIR)
        )
    )

    try:

        # ====================================================
        # MP3
        # ====================================================

        if audio_only:

            mp3_file, info = (
                await asyncio.to_thread(
                    download_audio_sync,
                    url,
                    work_dir
                )
            )

            await status.edit_text(
                "📤 MP3 yuborilmoqda..."
            )

            title = (
                info.get("title")
                or "audio"
            )

            performer = (
                info.get("artist")
                or info.get("uploader")
                or info.get("channel")
                or ""
            )

            with open(
                mp3_file,
                "rb"
            ) as audio_file:

                await context.bot.send_audio(
                    chat_id=chat_id,

                    audio=InputFile(
                        audio_file,
                        filename="audio.mp3"
                    ),

                    title=title,

                    performer=performer,

                    caption="🎵 MP3 128 kbps"
                )

            await status.delete()

            return

        # ====================================================
        # VIDEO
        # ====================================================

        source_file, info = (
            await asyncio.to_thread(
                download_video_sync,
                url,
                work_dir
            )
        )

        await status.edit_text(
            "⚙️ Video 480p / 14 MB ga tayyorlanmoqda..."
        )

        final_file = (
            work_dir / "final_480p.mp4"
        )

        await asyncio.to_thread(
            compress_video_sync,
            source_file,
            final_file
        )

        await status.edit_text(
            "📤 Video yuborilmoqda..."
        )

        with open(
            final_file,
            "rb"
        ) as video_file:

            await context.bot.send_video(
                chat_id=chat_id,

                video=InputFile(
                    video_file,
                    filename="video_480p.mp4"
                ),

                supports_streaming=True,

                caption=(
                    "🎬 480p\n"
                    "📦 14 MB gacha"
                )
            )

        await status.delete()

    except Exception as e:

        logger.exception(
            "VIDEO/AUDIO ERROR"
        )

        try:

            await status.edit_text(
                "❌ Yuklashda xato:\n\n"
                f"<code>{str(e)[:1800]}</code>",
                parse_mode="HTML"
            )

        except Exception:
            pass

    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context
):

    text = (
        "👋 <b>Media yuklovchi bot</b>\n\n"

        "🔗 Media havolasini yuboring.\n\n"

        "🎵 <b>Qo'shiq qidirish</b>\n"
        "Ijrochi yoki qo'shiq nomini yozing.\n\n"

        "🎙️ <b>Shazam</b>\n"
        "Voice yoki audio yuboring — "
        "qo'shiqni aniqlayman.\n\n"

        "📌 <b>YouTube</b>\n"
        "🎬 Video 480p\n"
        "🎵 MP3 128 kbps\n\n"

        "📦 Video maksimal 14 MB."
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# ============================================================
# TEXT
# ============================================================

async def handle_text(
    update,
    context
):

    if (
        not update.message
        or not update.message.text
    ):
        return

    text = update.message.text.strip()

    # ========================================================
    # URL
    # ========================================================

    urls = URL_RE.findall(text)

    if urls:

        url = urls[0]

        if is_supported_url(url):

            # YouTube
            if is_youtube(url):

                context.user_data[
                    "youtube_url"
                ] = url

                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🎬 VIDEO 480p",
                            callback_data="yt:video"
                        ),

                        InlineKeyboardButton(
                            "🎵 MP3 128 kbps",
                            callback_data="yt:mp3"
                        ),
                    ]
                ])

                await update.message.reply_text(
                    "🎬 <b>YouTube</b>\n\n"
                    "Formatni tanlang:",
                    parse_mode="HTML",
                    reply_markup=keyboard
                )

            # Boshqa saytlar
            else:

                await process_video(
                    update,
                    context,
                    url,
                    audio_only=False
                )

            return

    # ========================================================
    # QO'SHIQ QIDIRISH
    # ========================================================

    query_text = text

    status = await update.message.reply_text(
        "🔎 Qo'shiq qidirilmoqda..."
    )

    try:

        # YouTube va Google bir vaqtda
        youtube_task = asyncio.to_thread(
            youtube_search_sync,
            query_text
        )

        google_task = asyncio.to_thread(
            google_search_sync,
            query_text
        )

        youtube_results, google_results = (
            await asyncio.gather(
                youtube_task,
                google_task,
                return_exceptions=True
            )
        )

        if isinstance(
            youtube_results,
            Exception
        ):

            logger.exception(
                "YouTube search error",
                exc_info=youtube_results
            )

            youtube_results = []

        if isinstance(
            google_results,
            Exception
        ):

            logger.exception(
                "Google search error",
                exc_info=google_results
            )

            google_results = []

        results = youtube_results[:50]

        if not results:

            await status.edit_text(
                "❌ Qo'shiq topilmadi."
            )

            return

        # Update uchun ID
        # Muhim: Update.from_user emas!
        user_id = update.effective_user.id

        USER_SEARCH_DATA[user_id] = {
            "results": results,
            "google": google_results[:50],
            "query": query_text,
        }

        await status.delete()

        await update.message.reply_text(
            search_text(
                0,
                len(results)
            ),

            parse_mode="HTML",

            reply_markup=make_search_keyboard(
                user_id,
                0,
                len(results)
            )
        )

        if google_results:

            await update.message.reply_text(
                "🌐 Google'da ham natijalar mavjud.",

                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🌐 Google — 1-sahifa",
                            callback_data="google:0"
                        )
                    ]
                ])
            )

    except Exception as e:

        logger.exception(
            "SEARCH ERROR"
        )

        try:

            await status.edit_text(
                "❌ Qidirishda xato:\n\n"
                f"{str(e)[:1500]}"
            )

        except Exception:
            pass


# ============================================================
# VOICE / AUDIO → SHAZAM
# ============================================================

async def handle_audio(
    update,
    context
):

    message = update.message

    status = await message.reply_text(
        "🎙️ Shazam qo'shiqni aniqlayapti..."
    )

    work_dir = Path(
        tempfile.mkdtemp(
            prefix="shazam_",
            dir=str(DOWNLOAD_DIR)
        )
    )

    try:

        # ====================================================
        # VOICE
        # ====================================================

        if message.voice:

            telegram_file = (
                await message.voice.get_file()
            )

            input_file = (
                work_dir / "voice.ogg"
            )

            await telegram_file.download_to_drive(
                custom_path=str(input_file)
            )

        # ====================================================
        # AUDIO
        # ====================================================

        elif message.audio:

            telegram_file = (
                await message.audio.get_file()
            )

            extension = ".mp3"

            if message.audio.file_name:

                extension = (
                    Path(
                        message.audio.file_name
                    ).suffix
                    or ".mp3"
                )

            input_file = (
                work_dir
                / ("audio" + extension)
            )

            await telegram_file.download_to_drive(
                custom_path=str(input_file)
            )

        else:

            await status.delete()
            return

        # ====================================================
        # SHAZAM
        # ====================================================

        result = await recognize_shazam(
            str(input_file)
        )

        if not result:

            await status.edit_text(
                "❌ Shazam qo'shiqni aniqlay olmadi."
            )

            return

        user_id = (
            update.effective_user.id
        )

        USER_SEARCH_DATA[user_id] = {
            "results": [],
            "google": [],
            "query": result["query"],
            "shazam": result,
        }

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🎵 MP3 128 kbps",
                    callback_data="shazam:download"
                )
            ]
        ])

        await status.edit_text(
            "🎵 <b>Qo'shiq aniqlandi!</b>\n\n"
            f"👤 <b>{result['artist']}</b>\n"
            f"🎵 <b>{result['title']}</b>\n\n"
            "MP3 olish uchun knopkani bosing.",
            parse_mode="HTML",
            reply_markup=keyboard
        )

    except Exception as e:

        logger.exception(
            "SHAZAM ERROR"
        )

        try:

            await status.edit_text(
                "❌ Shazam xatosi:\n\n"
                f"<code>{str(e)[:1500]}</code>",
                parse_mode="HTML"
            )

        except Exception:
            pass

    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )


# ============================================================
# CALLBACK
# ============================================================

async def callback_handler(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    # MUHIM:
    # CallbackQuery foydalanuvchisi:
    # query.from_user.id

    user_id = query.from_user.id

    data = query.data

    # ========================================================
    # YOUTUBE VIDEO
    # ========================================================

    if data == "yt:video":

        url = context.user_data.get(
            "youtube_url"
        )

        if not url:

            await query.edit_message_text(
                "❌ YouTube havolasi topilmadi."
            )

            return

        try:
            await query.message.delete()
        except Exception:
            pass

        await process_video(
            update,
            context,
            url,
            audio_only=False
        )

        return

    # ========================================================
    # YOUTUBE MP3
    # ========================================================

    if data == "yt:mp3":

        url = context.user_data.get(
            "youtube_url"
        )

        if not url:

            await query.edit_message_text(
                "❌ YouTube havolasi topilmadi."
            )

            return

        try:
            await query.message.delete()
        except Exception:
            pass

        await process_video(
            update,
            context,
            url,
            audio_only=True
        )

        return

    # ========================================================
    # SEARCH PAGE
    # ========================================================

    if data.startswith("page:"):

        page_value = (
            data.split(":")[1]
        )

        if page_value == "current":
            return

        page = int(page_value)

        user_data = USER_SEARCH_DATA.get(
            user_id
        )

        if not user_data:

            await query.edit_message_text(
                "❌ Qidiruv natijalari eskirgan."
            )

            return

        results = user_data.get(
            "results",
            []
        )

        if not results:

            await query.edit_message_text(
                "❌ Natijalar topilmadi."
            )

            return

        await query.edit_message_text(
            search_text(
                page,
                len(results)
            ),

            parse_mode="HTML",

            reply_markup=make_search_keyboard(
                user_id,
                page,
                len(results)
            )
        )

        return

    # ========================================================
    # SONG BUTTON
    # ========================================================

    if data.startswith("song:"):

        try:

            index = int(
                data.split(":")[1]
            )

        except Exception:

            await query.edit_message_text(
                "❌ Noto'g'ri knopka."
            )

            return

        user_data = USER_SEARCH_DATA.get(
            user_id
        )

        if not user_data:

            await query.edit_message_text(
                "❌ Qidiruv natijasi topilmadi."
            )

            return

        results = user_data.get(
            "results",
            []
        )

        if (
            index < 0
            or index >= len(results)
        ):

            await query.edit_message_text(
                "❌ Bu natija mavjud emas."
            )

            return

        song = results[index]

        try:
            await query.message.delete()
        except Exception:
            pass

        await process_video(
            update,
            context,
            song["url"],
            audio_only=True
        )

        return

    # ========================================================
    # SHAZAM MP3
    # ========================================================

    if data == "shazam:download":

        user_data = USER_SEARCH_DATA.get(
            user_id
        )

        if not user_data:

            await query.edit_message_text(
                "❌ Shazam natijasi topilmadi."
            )

            return

        shazam_result = user_data.get(
            "shazam"
        )

        if not shazam_result:

            await query.edit_message_text(
                "❌ Shazam natijasi topilmadi."
            )

            return

        search_query = (
            shazam_result["query"]
        )

        try:

            await query.edit_message_text(
                "🔎 Qo'shiq topilmoqda..."
            )

            results = await asyncio.to_thread(
                youtube_search_sync,
                search_query
            )

            if not results:

                await query.edit_message_text(
                    "❌ Qo'shiq YouTube'dan topilmadi."
                )

                return

            url = results[0]["url"]

            try:
                await query.message.delete()
            except Exception:
                pass

            await process_video(
                update,
                context,
                url,
                audio_only=True
            )

        except Exception as e:

            logger.exception(
                "SHAZAM DOWNLOAD ERROR"
            )

            try:

                await query.edit_message_text(
                    "❌ Xato:\n"
                    f"{str(e)[:1000]}"
                )

            except Exception:
                pass

        return

    # ========================================================
    # GOOGLE
    # ========================================================

    if data.startswith("google:"):

        page_value = (
            data.split(":")[1]
        )

        if page_value == "current":
            return

        page = int(page_value)

        user_data = USER_SEARCH_DATA.get(
            user_id
        )

        if not user_data:

            await query.edit_message_text(
                "❌ Google natijalari topilmadi."
            )

            return

        results = user_data.get(
            "google",
            []
        )

        if not results:

            await query.edit_message_text(
                "❌ Google natijalari mavjud emas."
            )

            return

        start = page * 10

        end = min(
            start + 10,
            len(results)
        )

        keyboard = []

        for index in range(
            start,
            end
        ):

            item = results[index]

            title = item.get(
                "title",
                "Natija"
            )

            url = item.get(
                "url"
            )

            if not url:
                continue

            keyboard.append([
                InlineKeyboardButton(
                    f"🌐 {index + 1}. {title[:55]}",
                    url=url
                )
            ])

        navigation = []

        if page > 0:

            navigation.append(
                InlineKeyboardButton(
                    "⬅️",
                    callback_data=f"google:{page - 1}"
                )
            )

        navigation.append(
            InlineKeyboardButton(
                f"{page + 1}/5",
                callback_data="google:current"
            )
        )

        if (
            page < 4
            and end < len(results)
        ):

            navigation.append(
                InlineKeyboardButton(
                    "➡️",
                    callback_data=f"google:{page + 1}"
                )
            )

        keyboard.append(
            navigation
        )

        await query.edit_message_text(
            "🌐 <b>Google natijalari</b>\n\n"
            f"📄 {start + 1}-{end} / {len(results)}",

            parse_mode="HTML",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update,
    context
):

    logger.error(
        "Unhandled exception: %s",
        context.error,
        exc_info=context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN Railway Variables'da mavjud emas."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # START
    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # VOICE / AUDIO
    app.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO,
            handle_audio
        )
    )

    # TEXT
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    # BUTTONS
    app.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # ERRORS
    app.add_error_handler(
        error_handler
    )

    logger.info(
        "BOT ISHLAYAPTI..."
    )

    app.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# START BOT
# ============================================================

if __name__ == "__main__":
    main()
