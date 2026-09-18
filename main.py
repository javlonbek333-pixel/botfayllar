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

# Telegramga yuboriladigan maksimal hajm
TARGET_SIZE = 14 * 1024 * 1024

# Manbadan olinadigan maksimal fayl
MAX_SOURCE_SIZE = 1024 * 1024 * 1024

DOWNLOAD_DIR = Path("/tmp/bot_downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

FFMPEG_LOCATION = os.getenv(
    "FFMPEG_LOCATION",
    "/usr/bin"
)

GOOGLE_API_KEY = os.getenv(
    "GOOGLE_API_KEY"
)

GOOGLE_CX = os.getenv(
    "GOOGLE_CX"
)


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# YOUTUBE COOKIES
# ============================================================

def prepare_youtube_cookies():

    b64 = os.getenv(
        "YOUTUBE_COOKIES_B64"
    )

    plain = os.getenv(
        "YOUTUBE_COOKIES"
    )

    if not b64 and not plain:
        return None

    cookie_file = (
        DOWNLOAD_DIR / "youtube_cookies.txt"
    )

    try:

        if b64:
            data = base64.b64decode(
                b64
            ).decode("utf-8")
        else:
            data = plain

        cookie_file.write_text(
            data,
            encoding="utf-8"
        )

        return str(cookie_file)

    except Exception as e:

        logger.error(
            "Cookies xatosi: %s",
            e
        )

        return None


# ============================================================
# URL
# ============================================================

def is_youtube(url):

    return bool(
        re.search(
            r"(youtube\.com|youtu\.be)",
            url,
            re.IGNORECASE
        )
    )


def is_ok(url):

    return (
        "ok.ru" in url.lower()
        or "odnoklassniki.ru" in url.lower()
    )


def is_supported_url(url):

    domains = [
        "youtube.com",
        "youtu.be",
        "instagram.com",
        "tiktok.com",
        "facebook.com",
        "fb.watch",
        "ok.ru",
        "odnoklassniki.ru",
        "pinterest.com",
        "pin.it",
        "snapchat.com",
        "likee.video",
        "threads.net",
    ]

    url_lower = url.lower()

    return any(
        domain in url_lower
        for domain in domains
    )


# ============================================================
# YT-DLP SOZLAMALARI
# ============================================================

def base_ydl_options(url=None):

    options = {
        "quiet": True,
        "no_warnings": True,

        "noplaylist": True,

        "retries": 5,
        "fragment_retries": 5,

        "socket_timeout": 30,

        "concurrent_fragment_downloads": 8,

        "nocheckcertificate": True,

        "ffmpeg_location": FFMPEG_LOCATION,

        "max_filesize": MAX_SOURCE_SIZE,

        "geo_bypass": True,

        "http_headers": {
            "User-Agent":
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/138.0.0.0 "
                "Safari/537.36"
        },
    }

    # YouTube
    if url and is_youtube(url):

        cookie_file = (
            prepare_youtube_cookies()
        )

        if cookie_file:
            options["cookiefile"] = cookie_file

        options["extractor_args"] = {
            "youtube": {
                "player_client": [
                    "android",
                    "web"
                ]
            }
        }

    return options


# ============================================================
# FAYL TOPISH
# ============================================================

def find_downloaded_file(
    folder,
    extensions=None
):

    files = []

    for file in folder.iterdir():

        if not file.is_file():
            continue

        if extensions:

            if file.suffix.lower() not in extensions:
                continue

        files.append(file)

    if not files:
        return None

    return max(
        files,
        key=lambda x: x.stat().st_size
    )


# ============================================================
# VIDEO YUKLASH
# ============================================================

def download_video_sync(
    url,
    folder
):

    options = base_ydl_options(url)

    options.update({

        "format":
            "bestvideo+bestaudio/"
            "best",

        "merge_output_format":
            "mp4",

        "outtmpl":
            str(
                folder /
                "%(id)s.%(ext)s"
            ),

        "overwrites": True,

    })

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        filename = ydl.prepare_filename(
            info
        )

        path = Path(
            filename
        )

        if path.exists():
            return path

    return find_downloaded_file(
        folder,
        {
            ".mp4",
            ".mkv",
            ".webm",
            ".mov"
        }
    )


# ============================================================
# AUDIO YUKLASH
# ============================================================

def download_audio_sync(
    url,
    folder
):

    options = base_ydl_options(url)

    options.update({

        "format":
            "bestaudio/best",

        "outtmpl":
            str(
                folder /
                "%(id)s.%(ext)s"
            ),

        "postprocessors": [
            {
                "key":
                    "FFmpegExtractAudio",

                "preferredcodec":
                    "mp3",

                "preferredquality":
                    "128",
            }
        ],

        "overwrites":
            True,

    })

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        original = Path(
            ydl.prepare_filename(
                info
            )
        )

        mp3 = original.with_suffix(
            ".mp3"
        )

        if mp3.exists():
            return mp3

    return find_downloaded_file(
        folder,
        {".mp3"}
    )


# ============================================================
# VIDEO DAVOMIYLIGI
# ============================================================

def get_duration(
    file_path
):

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
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        value = result.stdout.strip()

        if value:
            return float(value)

    except Exception as e:

        logger.error(
            "ffprobe xatosi: %s",
            e
        )

    return 60.0


# ============================================================
# VIDEO SIQISH
#
# MUHIM:
# Video o'lchami o'zgartirilmaydi.
# 480p YO'Q.
# Faqat bitrate kamaytiriladi.
# ============================================================

def compress_video_sync(
    input_file,
    output_file
):

    duration = get_duration(
        input_file
    )

    if duration <= 0:
        duration = 60

    # 14 MiB chegaraga ozgina zaxira
    target_bytes = int(
        TARGET_SIZE * 0.90
    )

    # Audio
    audio_kbps = 48

    # Dastlabki video bitrate
    total_kbps = int(
        (target_bytes * 8)
        / duration
        / 1000
    )

    video_kbps = max(
        80,
        total_kbps - audio_kbps
    )

    # Har safar yanada pasayadi
    attempts = [
        1.00,
        0.75,
        0.55,
        0.40,
        0.30,
        0.22,
        0.16,
        0.11,
        0.07,
    ]

    last_size = 0

    for number, multiplier in enumerate(
        attempts,
        start=1
    ):

        bitrate = max(
            40,
            int(
                video_kbps *
                multiplier
            )
        )

        temp_file = (
            output_file.parent /
            f"encoded_{number}.mp4"
        )

        logger.info(
            "Encode #%s, video bitrate=%sk",
            number,
            bitrate
        )

        command = [
            "ffmpeg",
            "-y",

            "-i",
            str(input_file),

            # O'LCHAM O'ZGARTIRILMAYDI
            "-c:v",
            "libx264",

            "-preset",
            "ultrafast",

            "-b:v",
            f"{bitrate}k",

            "-maxrate",
            f"{bitrate}k",

            "-bufsize",
            f"{bitrate * 2}k",

            "-c:a",
            "aac",

            "-b:a",
            f"{audio_kbps}k",

            "-movflags",
            "+faststart",

            str(temp_file),
        ]

        try:

            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )

            if result.returncode != 0:

                logger.error(
                    "FFmpeg xatosi:\n%s",
                    result.stderr[-3000:]
                )

                temp_file.unlink(
                    missing_ok=True
                )

                continue

            if not temp_file.exists():
                continue

            last_size = (
                temp_file.stat().st_size
            )

            logger.info(
                "Natija hajmi: %.2f MiB",
                last_size /
                1024 /
                1024
            )

            # 14 MiB ga tushdi
            if last_size <= TARGET_SIZE:

                output_file.unlink(
                    missing_ok=True
                )

                temp_file.rename(
                    output_file
                )

                return output_file

            # Keyingi urinish
            temp_file.unlink(
                missing_ok=True
            )

        except Exception as e:

            logger.error(
                "Encode exception: %s",
                e
            )

            temp_file.unlink(
                missing_ok=True
            )

    raise RuntimeError(
        "Video hajmini 14 MB dan pastga "
        "tushirib bo'lmadi. "
        f"Oxirgi hajm: "
        f"{last_size / 1024 / 1024:.2f} MiB"
    )


# ============================================================
# YOUTUBE QIDIRUV
# ============================================================

def youtube_search_sync(
    query
):

    options = base_ydl_options()

    options.update({
        "extract_flat": True,
        "skip_download": True,
        "playlistend": 50,
    })

    results = []

    try:

        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            data = ydl.extract_info(
                f"ytsearch50:{query}",
                download=False
            )

            entries = data.get(
                "entries",
                []
            )

            for item in entries:

                if not item:
                    continue

                title = item.get(
                    "title",
                    "Noma'lum"
                )

                url = (
                    item.get(
                        "webpage_url"
                    )
                    or item.get(
                        "url"
                    )
                )

                video_id = item.get(
                    "id"
                )

                if (
                    not url
                    and video_id
                ):

                    url = (
                        "https://www.youtube.com/watch?v="
                        + video_id
                    )

                if url:

                    results.append({
                        "title": title,
                        "url": url
                    })

    except Exception as e:

        logger.error(
            "YouTube search xatosi: %s",
            e
        )

    return results[:50]


# ============================================================
# GOOGLE QIDIRUV
# ============================================================

def google_search_sync(
    query
):

    if (
        not GOOGLE_API_KEY
        or not GOOGLE_CX
    ):
        return []

    results = []

    try:

        for start in [
            1,
            11,
            21,
            31,
            41
        ]:

            response = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key":
                        GOOGLE_API_KEY,

                    "cx":
                        GOOGLE_CX,

                    "q":
                        query,

                    "start":
                        start,

                    "num":
                        10,
                },
                timeout=20,
            )

            response.raise_for_status()

            data = response.json()

            for item in data.get(
                "items",
                []
            ):

                link = item.get(
                    "link"
                )

                if link:

                    results.append({
                        "title":
                            item.get(
                                "title",
                                "Natija"
                            ),

                        "url":
                            link
                    })

    except Exception as e:

        logger.error(
            "Google xatosi: %s",
            e
        )

    return results[:50]


# ============================================================
# SHAZAM
# ============================================================

async def recognize_shazam(
    file_path
):

    try:

        shazam = Shazam()

        result = await shazam.recognize(
            str(file_path)
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

        if not artist:
            artist = "Noma'lum"

        return {
            "title":
                title,

            "artist":
                artist,

            "query":
                f"{artist} {title}",
        }

    except Exception as e:

        logger.error(
            "Shazam xatosi: %s",
            e
        )

        return None


# ============================================================
# SEARCH DATA
# ============================================================

USER_SEARCH_DATA = {}

PAGE_SIZE = 10
MAX_PAGES = 5


def make_search_keyboard(
    user_id,
    page=0
):

    data = USER_SEARCH_DATA.get(
        user_id,
        {}
    )

    results = data.get(
        "youtube",
        []
    )

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE

    buttons = []

    for index in range(
        start,
        min(end, len(results))
    ):

        title = results[index][
            "title"
        ]

        if len(title) > 55:
            title = title[:55] + "..."

        buttons.append([
            InlineKeyboardButton(
                f"🎵 {title}",
                callback_data=f"song:{index}"
            )
        ])

    navigation = []

    if page > 0:

        navigation.append(
            InlineKeyboardButton(
                "⬅️ Oldingi",
                callback_data=f"page:{page - 1}"
            )
        )

    if (
        page < MAX_PAGES - 1
        and end < len(results)
    ):

        navigation.append(
            InlineKeyboardButton(
                "Keyingi ➡️",
                callback_data=f"page:{page + 1}"
            )
        )

    if navigation:
        buttons.append(
            navigation
        )

    if data.get(
        "google"
    ):

        buttons.append([
            InlineKeyboardButton(
                "🌐 Google",
                callback_data="google:current"
            )
        ])

    return InlineKeyboardMarkup(
        buttons
    )


# ============================================================
# VIDEO PROCESS
# ============================================================

async def process_video(
    update,
    context,
    url,
    mode="video"
):

    message = update.effective_message

    work_dir = Path(
        tempfile.mkdtemp(
            dir=DOWNLOAD_DIR
        )
    )

    try:

        # ====================================================
        # MP3
        # ====================================================

        if mode == "mp3":

            await message.reply_text(
                "⏳ Yuklanmoqda..."
            )

            mp3_file = await asyncio.to_thread(
                download_audio_sync,
                url,
                work_dir
            )

            if (
                not mp3_file
                or not mp3_file.exists()
            ):
                raise RuntimeError(
                    "MP3 fayl topilmadi."
                )

            with mp3_file.open(
                "rb"
            ) as f:

                await message.reply_audio(
                    audio=InputFile(
                        f,
                        filename="audio.mp3"
                    ),

                    caption=
                        "@yuklatgbot "
                        "orqali yuklab olindi",
                )

            return

        # ====================================================
        # VIDEO
        # ====================================================

        await message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        source_file = await asyncio.to_thread(
            download_video_sync,
            url,
            work_dir
        )

        if (
            not source_file
            or not source_file.exists()
        ):

            raise RuntimeError(
                "Video fayl topilmadi."
            )

        final_file = (
            work_dir /
            "final.mp4"
        )

        await asyncio.to_thread(
            compress_video_sync,
            source_file,
            final_file
        )

        if (
            not final_file.exists()
        ):

            raise RuntimeError(
                "Siqilgan video topilmadi."
            )

        if (
            final_file.stat().st_size
            > TARGET_SIZE
        ):

            raise RuntimeError(
                "Video 14 MiB dan katta."
            )

        with final_file.open(
            "rb"
        ) as f:

            await message.reply_video(
                video=InputFile(
                    f,
                    filename="video.mp4"
                ),

                caption=
                    "@yuklatgbot "
                    "orqali yuklab olindi",

                supports_streaming=True,
            )

    except Exception as e:

        logger.exception(
            "Media xatosi"
        )

        await message.reply_text(
            "❌ Xatolik yuz berdi:\n\n"
            f"{str(e)[:1500]}"
        )

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
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "👋 Assalomu alaykum!\n\n"
        "📥 Media yuklash uchun "
        "havolani yuboring.\n\n"

        "▶️ YouTube\n"
        "📸 Instagram\n"
        "🎵 TikTok\n"
        "📘 Facebook\n"
        "🟠 OK.ru\n"
        "📌 Pinterest\n"
        "👻 Snapchat\n"
        "❤️ Likee\n"
        "🧵 Threads\n\n"

        "🎵 Qo‘shiq qidirish uchun "
        "qo‘shiq nomi yoki ijrochi nomini yuboring.\n\n"

        "🎙️ Audio yoki voice yuborsangiz, "
        "Shazam orqali aniqlaymiz."
    )


# ============================================================
# TEXT
# ============================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        update.message.text or ""
    ).strip()

    if not text:
        return

    # ========================================================
    # URL
    # ========================================================

    if is_supported_url(text):

        # YouTube
        if is_youtube(text):

            context.user_data[
                "youtube_url"
            ] = text

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎬 VIDEO",
                        callback_data="yt:video"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🎵 MP3",
                        callback_data="yt:mp3"
                    )
                ],
            ])

            await update.message.reply_text(
                "Havola qabul qilindi:",
                reply_markup=keyboard
            )

        else:

            await process_video(
                update,
                context,
                text,
                mode="video"
            )

        return

    # ========================================================
    # QO'SHIQ QIDIRISH
    # ========================================================

    status = await update.message.reply_text(
        "🔎 Qidirilmoqda..."
    )

    try:

        youtube_results, google_results = (
            await asyncio.gather(

                asyncio.to_thread(
                    youtube_search_sync,
                    text
                ),

                asyncio.to_thread(
                    google_search_sync,
                    text
                ),
            )
        )

        user_id = (
            update.effective_user.id
        )

        USER_SEARCH_DATA[
            user_id
        ] = {

            "query":
                text,

            "youtube":
                youtube_results,

            "google":
                google_results,

            "page":
                0,
        }

        if not youtube_results:

            await status.edit_text(
                "❌ Qo‘shiq topilmadi."
            )

            return

        await status.edit_text(
            f"🎵 {len(youtube_results)} "
            "ta natija topildi.\n\n"
            "10 tadan:",
            reply_markup=
                make_search_keyboard(
                    user_id,
                    0
                )
        )

    except Exception as e:

        logger.exception(
            "Search xatosi"
        )

        await status.edit_text(
            "❌ Qidiruvda xatolik:\n"
            f"{str(e)[:1000]}"
        )


# ============================================================
# VOICE / AUDIO
# ============================================================

async def handle_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.message

    status = await message.reply_text(
        "🎙️ Qo‘shiq aniqlanmoqda..."
    )

    work_dir = Path(
        tempfile.mkdtemp(
            dir=DOWNLOAD_DIR
        )
    )

    try:

        # Voice
        if message.voice:

            telegram_file = (
                await context.bot.get_file(
                    message.voice.file_id
                )
            )

            input_file = (
                work_dir /
                "voice.ogg"
            )

            await telegram_file.download_to_drive(
                custom_path=str(
                    input_file
                )
            )

        # Audio
        elif message.audio:

            telegram_file = (
                await context.bot.get_file(
                    message.audio.file_id
                )
            )

            filename = (
                message.audio.file_name
                or "audio.mp3"
            )

            ext = (
                Path(filename).suffix
                or ".mp3"
            )

            input_file = (
                work_dir /
                f"audio{ext}"
            )

            await telegram_file.download_to_drive(
                custom_path=str(
                    input_file
                )
            )

        else:

            await status.edit_text(
                "❌ Audio topilmadi."
            )

            return

        result = await recognize_shazam(
            input_file
        )

        if not result:

            await status.edit_text(
                "❌ Qo‘shiq aniqlanmadi."
            )

            return

        context.user_data[
            "shazam_result"
        ] = result

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🎵 MP3",
                    callback_data=
                        "shazam:download"
                )
            ]
        ])

        await status.edit_text(
            "🎵 Qo‘shiq aniqlandi!\n\n"
            f"👤 {result['artist']}\n"
            f"🎵 {result['title']}",
            reply_markup=keyboard
        )

    except Exception as e:

        logger.exception(
            "Shazam xatosi"
        )

        await status.edit_text(
            "❌ Audio aniqlashda xatolik:\n"
            f"{str(e)[:1000]}"
        )

    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )


# ============================================================
# CALLBACK
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

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

            await query.message.reply_text(
                "❌ Havola topilmadi."
            )

            return

        await process_video(
            update,
            context,
            url,
            mode="video"
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

            await query.message.reply_text(
                "❌ Havola topilmadi."
            )

            return

        await process_video(
            update,
            context,
            url,
            mode="mp3"
        )

        return

    # ========================================================
    # PAGE
    # ========================================================

    if data.startswith(
        "page:"
    ):

        try:

            page = int(
                data.split(":")[1]
            )

        except Exception:

            page = 0

        search_data = (
            USER_SEARCH_DATA.get(
                user_id
            )
        )

        if not search_data:

            await query.message.reply_text(
                "❌ Qidiruv ma'lumoti topilmadi."
            )

            return

        search_data[
            "page"
        ] = page

        await query.edit_message_reply_markup(
            reply_markup=
                make_search_keyboard(
                    user_id,
                    page
                )
        )

        return

    # ========================================================
    # SONG
    # ========================================================

    if data.startswith(
        "song:"
    ):

        try:

            index = int(
                data.split(":")[1]
            )

        except Exception:

            await query.message.reply_text(
                "❌ Natija xatosi."
            )

            return

        search_data = (
            USER_SEARCH_DATA.get(
                user_id
            )
        )

        if not search_data:

            await query.message.reply_text(
                "❌ Qidiruv eskirgan."
            )

            return

        results = search_data.get(
            "youtube",
            []
        )

        if (
            index < 0
            or index >= len(results)
        ):

            await query.message.reply_text(
                "❌ Natija topilmadi."
            )

            return

        selected = results[index]

        await query.message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        work_dir = Path(
            tempfile.mkdtemp(
                dir=DOWNLOAD_DIR
            )
        )

        try:

            mp3_file = (
                await asyncio.to_thread(
                    download_audio_sync,
                    selected["url"],
                    work_dir
                )
            )

            if (
                not mp3_file
                or not mp3_file.exists()
            ):

                raise RuntimeError(
                    "MP3 topilmadi."
                )

            with mp3_file.open(
                "rb"
            ) as f:

                await query.message.reply_audio(
                    audio=InputFile(
                        f,
                        filename="song.mp3"
                    ),

                    caption=
                        "@yuklatgbot "
                        "orqali yuklab olindi",
                )

        except Exception as e:

            logger.exception(
                "Song xatosi"
            )

            await query.message.reply_text(
                "❌ MP3 yuklashda xatolik:\n"
                f"{str(e)[:1200]}"
            )

        finally:

            shutil.rmtree(
                work_dir,
                ignore_errors=True
            )

        return

    # ========================================================
    # SHAZAM DOWNLOAD
    # ========================================================

    if data == "shazam:download":

        result = context.user_data.get(
            "shazam_result"
        )

        if not result:

            await query.message.reply_text(
                "❌ Shazam natijasi topilmadi."
            )

            return

        await query.message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        work_dir = Path(
            tempfile.mkdtemp(
                dir=DOWNLOAD_DIR
            )
        )

        try:

            results = await asyncio.to_thread(
                youtube_search_sync,
                result["query"]
            )

            if not results:

                raise RuntimeError(
                    "YouTube'dan qo‘shiq topilmadi."
                )

            mp3_file = await asyncio.to_thread(
                download_audio_sync,
                results[0]["url"],
                work_dir
            )

            if (
                not mp3_file
                or not mp3_file.exists()
            ):

                raise RuntimeError(
                    "MP3 topilmadi."
                )

            with mp3_file.open(
                "rb"
            ) as f:

                await query.message.reply_audio(
                    audio=InputFile(
                        f,
                        filename="song.mp3"
                    ),

                    caption=
                        "@yuklatgbot "
                        "orqali yuklab olindi",
                )

        except Exception as e:

            logger.exception(
                "Shazam download xatosi"
            )

            await query.message.reply_text(
                "❌ MP3 yuklashda xatolik:\n"
                f"{str(e)[:1200]}"
            )

        finally:

            shutil.rmtree(
                work_dir,
                ignore_errors=True
            )

        return

    # ========================================================
    # GOOGLE
    # ========================================================

    if data == "google:current":

        search_data = (
            USER_SEARCH_DATA.get(
                user_id
            )
        )

        if not search_data:

            await query.message.reply_text(
                "❌ Google natijalari topilmadi."
            )

            return

        google_results = (
            search_data.get(
                "google",
                []
            )
        )

        if not google_results:

            await query.message.reply_text(
                "❌ Google natijalari mavjud emas."
            )

            return

        buttons = []

        for item in google_results[:10]:

            title = item.get(
                "title",
                "Natija"
            )

            if len(title) > 55:
                title = (
                    title[:55]
                    + "..."
                )

            url = item.get(
                "url"
            )

            if url:

                buttons.append([
                    InlineKeyboardButton(
                        f"🌐 {title}",
                        url=url
                    )
                ])

        await query.message.reply_text(
            "🌐 Google natijalari:",
            reply_markup=
                InlineKeyboardMarkup(
                    buttons
                )
        )

        return


# ============================================================
# ERROR
# ============================================================

async def error_handler(
    update,
    context
):

    logger.error(
        "Telegram xatosi: %s",
        context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN Railway Variables'da yo‘q!"
        )

    # FFmpeg tekshirish
    try:

        result = subprocess.run(
            [
                "ffmpeg",
                "-version"
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.stdout:

            logger.info(
                result.stdout.splitlines()[0]
            )

    except Exception as e:

        logger.error(
            "FFmpeg topilmadi: %s",
            e
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # START
    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # CALLBACK
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # VOICE
    application.add_handler(
        MessageHandler(
            filters.VOICE,
            handle_audio
        )
    )

    # AUDIO
    application.add_handler(
        MessageHandler(
            filters.AUDIO,
            handle_audio
        )
    )

    # TEXT
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_text
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "BOT ISHLADI"
    )

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
