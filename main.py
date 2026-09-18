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


# =========================================================
# SOZLAMALAR
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

TARGET_SIZE = 14 * 1024 * 1024
MAX_SOURCE_SIZE = 1024 * 1024 * 1024

DOWNLOAD_DIR = Path("/tmp/bot_downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

FFMPEG_LOCATION = os.getenv(
    "FFMPEG_LOCATION",
    "/usr/bin"
)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")

YOUTUBE_COOKIES_B64 = os.getenv(
    "YOUTUBE_COOKIES_B64"
)

YOUTUBE_COOKIES = os.getenv(
    "YOUTUBE_COOKIES"
)

YOUTUBE_POT_URL = os.getenv(
    "YOUTUBE_POT_URL",
    "http://127.0.0.1:4416"
)

CAPTION = "@yuklatgbot orqali yuklab olindi"


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# =========================================================
# YOUTUBE COOKIES
# =========================================================

def prepare_youtube_cookies():
    """
    Railway Variables orqali:
      YOUTUBE_COOKIES_B64
    yoki
      YOUTUBE_COOKIES

    berilsa, vaqtinchalik cookies fayl yaratadi.
    """

    try:

        if YOUTUBE_COOKIES_B64:

            cookie_file = (
                DOWNLOAD_DIR /
                "youtube_cookies.txt"
            )

            data = base64.b64decode(
                YOUTUBE_COOKIES_B64
            )

            cookie_file.write_bytes(data)

            return str(cookie_file)

        if YOUTUBE_COOKIES:

            cookie_file = (
                DOWNLOAD_DIR /
                "youtube_cookies.txt"
            )

            cookie_file.write_text(
                YOUTUBE_COOKIES,
                encoding="utf-8"
            )

            return str(cookie_file)

    except Exception as e:

        logger.error(
            "Cookies xatosi: %s",
            e
        )

    return None


# =========================================================
# URL TEKSHIRISH
# =========================================================

def is_youtube(url):
    return bool(
        re.search(
            r"(youtube\.com|youtu\.be)",
            url,
            re.I
        )
    )


def is_instagram(url):
    return "instagram.com" in url.lower()


def is_tiktok(url):
    return (
        "tiktok.com" in url.lower()
        or "vm.tiktok.com" in url.lower()
    )


def is_facebook(url):
    return (
        "facebook.com" in url.lower()
        or "fb.watch" in url.lower()
    )


def is_ok(url):
    return (
        "ok.ru" in url.lower()
        or "odnoklassniki.ru" in url.lower()
    )


def is_supported_url(url):

    return any([
        is_youtube(url),
        is_instagram(url),
        is_tiktok(url),
        is_facebook(url),
        is_ok(url),
        "pinterest." in url.lower(),
        "snapchat." in url.lower(),
        "likee." in url.lower(),
        "threads." in url.lower(),
    ])


# =========================================================
# YT-DLP ASOSIY SOZLAMALARI
# =========================================================

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

        "ffmpeg_location":
            FFMPEG_LOCATION,

        "max_filesize":
            MAX_SOURCE_SIZE,

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

    if url and is_youtube(url):

        cookie_file = (
            prepare_youtube_cookies()
        )

        if cookie_file:

            options["cookiefile"] = (
                cookie_file
            )

        # YouTube hozirgi extractor
        # bilan ishlashga mos.
        options["extractor_args"] = {

            "youtube": {

                "player_client": [
                    "mweb",
                    "web_embedded",
                    "tv"
                ]
            }
        }

        # PO Token Provider server
        if YOUTUBE_POT_URL:

            options[
                "extractor_args"
            ][
                "youtubepot-bgutilhttp"
            ] = {
                "base_url":
                    YOUTUBE_POT_URL
            }

    return options


# =========================================================
# FAYL TOPISH
# =========================================================

def find_downloaded_file(workdir):

    files = []

    for p in workdir.iterdir():

        if not p.is_file():
            continue

        if p.suffix.lower() in [
            ".part",
            ".ytdl",
            ".temp",
        ]:
            continue

        files.append(p)

    if not files:
        return None

    files.sort(
        key=lambda x:
            x.stat().st_mtime,
        reverse=True
    )

    return files[0]


# =========================================================
# VIDEO YUKLASH
# =========================================================

def download_video_sync(
    url,
    workdir
):

    output_template = (
        str(workdir / "%(title).150s.%(ext)s")
    )

    options = base_ydl_options(url)

    options.update({

        "outtmpl":
            output_template,

        "format":
            "bestvideo+bestaudio/best",

        "merge_output_format":
            "mp4",

        "postprocessors": [],

    })

    logger.info(
        "Video yuklanmoqda: %s",
        url
    )

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        ydl.download([url])

    file_path = (
        find_downloaded_file(workdir)
    )

    if not file_path:

        raise RuntimeError(
            "Video fayli topilmadi."
        )

    if (
        file_path.stat().st_size
        > MAX_SOURCE_SIZE
    ):

        raise RuntimeError(
            "Manba video 1 GB dan katta."
        )

    return file_path


# =========================================================
# OK.RU MAXSUS YUKLASH
# =========================================================

def download_ok_video_sync(
    url,
    workdir
):

    output_template = (
        str(workdir / "%(title).150s.%(ext)s")
    )

    options = base_ydl_options(url)

    options.update({

        "outtmpl":
            output_template,

        "format":
            "best[ext=mp4]/best",

        "http_headers": {
            "User-Agent":
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/138.0.0.0 "
                "Safari/537.36",

            "Accept":
                "*/*",

            "Accept-Language":
                "en-US,en;q=0.9",
        },
    })

    logger.info(
        "OK.ru yuklanmoqda: %s",
        url
    )

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        ydl.download([url])

    file_path = (
        find_downloaded_file(workdir)
    )

    if not file_path:

        raise RuntimeError(
            "OK.ru video topilmadi."
        )

    return file_path


# =========================================================
# AUDIO / MP3
# =========================================================

def download_audio_sync(
    url,
    workdir
):

    output_template = (
        str(workdir / "%(title).150s.%(ext)s")
    )

    options = base_ydl_options(url)

    options.update({

        "outtmpl":
            output_template,

        "format":
            "bestaudio/best",

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
    })

    logger.info(
        "MP3 yuklanmoqda: %s",
        url
    )

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        ydl.download([url])

    file_path = (
        find_downloaded_file(workdir)
    )

    if not file_path:

        raise RuntimeError(
            "MP3 fayli topilmadi."
        )

    return file_path


# =========================================================
# VIDEO DAVOMIYLIGI
# =========================================================

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

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return 60

    try:
        return max(
            float(result.stdout.strip()),
            1
        )
    except:
        return 60


# =========================================================
# VIDEO O'LCHAMINI OLISH
# =========================================================

def get_video_dimensions(
    file_path
):

    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=s=x:p=0",
        str(file_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None, None

    try:

        width, height = (
            result.stdout.strip()
            .split("x")
        )

        return (
            int(width),
            int(height)
        )

    except:

        return None, None


# =========================================================
# VIDEO SIQISH
# =========================================================

def compress_video_sync(
    input_file,
    output_file
):

    """
    MUHIM:

    scale FILTER YO'Q.

    Shuning uchun:
      1920x1080 -> 1920x1080
      1280x720  -> 1280x720
      1080x1920 -> 1080x1920

    O'lcham o'zgarmaydi.

    Faqat:
      CRF
      video bitrate
      audio bitrate

    orqali hajm kamayadi.
    """

    width, height = (
        get_video_dimensions(
            input_file
        )
    )

    logger.info(
        "Original video o'lchami: %sx%s",
        width,
        height
    )

    # Dastlab yuqoriroq sifatdan boshlaymiz.
    crf_values = [
        23,
        26,
        29,
        32,
        35,
        38,
        41,
        44,
        47,
        50,
        53,
    ]

    audio_bitrates = [
        "64k",
        "48k",
        "40k",
        "32k",
        "32k",
        "24k",
        "24k",
        "24k",
        "24k",
        "20k",
        "20k",
    ]

    last_size = 0

    for crf, audio_bitrate in zip(
        crf_values,
        audio_bitrates
    ):

        temp_file = (
            output_file.with_name(
                f"compressed_{crf}.mp4"
            )
        )

        command = [

            "ffmpeg",

            "-y",

            "-i",
            str(input_file),

            # =====================================
            # MUHIM:
            # BU YERDA SCALE YO'Q
            # =====================================

            "-map",
            "0:v:0",

            "-map",
            "0:a:0?",

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-crf",
            str(crf),

            # Rang va pixel format
            # original ko'rinishga yaqin
            "-pix_fmt",
            "yuv420p",

            "-c:a",
            "aac",

            "-b:a",
            audio_bitrate,

            "-ac",
            "2",

            "-movflags",
            "+faststart",

            str(temp_file),
        ]

        logger.info(
            "Encode: CRF=%s",
            crf
        )

        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:

            logger.error(
                "FFmpeg xatosi:\n%s",
                result.stderr[-4000:]
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
            "CRF=%s -> %.2f MiB",
            crf,
            last_size /
            1024 /
            1024
        )

        if last_size <= TARGET_SIZE:

            output_file.unlink(
                missing_ok=True
            )

            temp_file.rename(
                output_file
            )

            # Tekshiramiz:
            new_width, new_height = (
                get_video_dimensions(
                    output_file
                )
            )

            logger.info(
                "Yakuniy o'lcham: %sx%s",
                new_width,
                new_height
            )

            if (
                width
                and height
                and (
                    new_width != width
                    or new_height != height
                )
            ):

                raise RuntimeError(
                    "Video o'lchami o'zgardi."
                )

            return output_file

        temp_file.unlink(
            missing_ok=True
        )

    raise RuntimeError(
        "Videoni 14 MiB gacha siqib "
        "bo'lmadi. Oxirgi hajm: "
        f"{last_size / 1024 / 1024:.2f} MiB"
    )


# =========================================================
# YOUTUBE SEARCH
# =========================================================

def youtube_search_sync(
    query
):

    options = base_ydl_options()

    options.update({

        "extract_flat":
            True,

        "skip_download":
            True,

    })

    results = []

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        data = ydl.extract_info(
            f"ytsearch50:{query}",
            download=False
        )

    for item in (
        data.get("entries", [])
        if data
        else []
    ):

        if not item:
            continue

        video_id = item.get("id")

        if not video_id:
            continue

        results.append({

            "title":
                item.get(
                    "title",
                    "Noma'lum"
                ),

            "url":
                f"https://www.youtube.com/watch?v={video_id}",

            "id":
                video_id,
        })

    return results[:50]


# =========================================================
# GOOGLE SEARCH
# =========================================================

def google_search_sync(
    query
):

    if not GOOGLE_API_KEY:
        return []

    if not GOOGLE_CX:
        return []

    results = []

    for start in [
        1,
        11,
        21,
        31,
        41
    ]:

        params = {

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
        }

        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
            timeout=20,
        )

        if response.status_code != 200:
            continue

        data = response.json()

        for item in data.get(
            "items",
            []
        ):

            results.append({

                "title":
                    item.get(
                        "title",
                        "Natija"
                    ),

                "url":
                    item.get(
                        "link"
                    ),
            })

    return results[:50]


# =========================================================
# SHAZAM
# =========================================================

async def recognize_shazam(
    file_path
):

    shazam = Shazam()

    result = await shazam.recognize(
        str(file_path)
    )

    track = result.get(
        "track"
    )

    if not track:
        return None

    title = track.get(
        "title"
    )

    artist = track.get(
        "subtitle"
    )

    if not title:
        return None

    return {

        "title":
            title,

        "artist":
            artist or "",

    }


# =========================================================
# SEARCH DATA
# =========================================================

USER_SEARCH_DATA = {}


def make_page_buttons(
    user_id,
    page,
    total
):

    buttons = []

    max_page = (
        total + 9
    ) // 10

    if max_page <= 1:
        return buttons

    row = []

    if page > 0:

        row.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=
                    f"page:{page-1}"
            )
        )

    row.append(
        InlineKeyboardButton(
            f"{page+1}/{max_page}",
            callback_data=
                "noop"
        )
    )

    if page < max_page - 1:

        row.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=
                    f"page:{page+1}"
            )
        )

    buttons.append(row)

    return buttons


def build_search_keyboard(
    user_id,
    page
):

    data = USER_SEARCH_DATA.get(
        user_id
    )

    if not data:
        return None

    results = data["results"]

    start = page * 10

    current = results[
        start:start + 10
    ]

    buttons = []

    for i, item in enumerate(
        current,
        start=start
    ):

        title = item.get(
            "title",
            "Noma'lum"
        )

        title = title[:45]

        buttons.append([
            InlineKeyboardButton(
                f"🎵 {i+1}. {title}",
                callback_data=
                    f"song:{i}"
            )
        ])

    buttons.extend(
        make_page_buttons(
            user_id,
            page,
            len(results)
        )
    )

    return InlineKeyboardMarkup(
        buttons
    )


# =========================================================
# VIDEO PROCESS
# =========================================================

async def process_video(
    update,
    context,
    url,
    audio=False
):

    user = update.effective_user

    if not user:
        return

    workdir = Path(
        tempfile.mkdtemp(
            prefix="media_",
            dir=DOWNLOAD_DIR
        )
    )

    status_message = None

    try:

        status_message = (
            await update.message.reply_text(
                "⏳ Yuklanmoqda..."
            )
        )

        if audio:

            file_path = await asyncio.to_thread(
                download_audio_sync,
                url,
                workdir
            )

            with open(
                file_path,
                "rb"
            ) as f:

                await update.message.reply_audio(
                    audio=InputFile(
                        f,
                        filename=
                            file_path.name
                    ),
                    caption=CAPTION
                )

            return

        # OK.ru alohida
        if is_ok(url):

            source_file = (
                await asyncio.to_thread(
                    download_ok_video_sync,
                    url,
                    workdir
                )
            )

        else:

            source_file = (
                await asyncio.to_thread(
                    download_video_sync,
                    url,
                    workdir
                )
            )

        final_file = (
            workdir /
            "final_video.mp4"
        )

        await asyncio.to_thread(
            compress_video_sync,
            source_file,
            final_file
        )

        # Yakuniy tekshiruv
        final_size = (
            final_file.stat().st_size
        )

        if final_size > TARGET_SIZE:

            raise RuntimeError(
                "Yakuniy video 14 MiB dan katta."
            )

        with open(
            final_file,
            "rb"
        ) as f:

            await update.message.reply_video(
                video=InputFile(
                    f,
                    filename="video.mp4"
                ),
                caption=CAPTION,

                # Telegram preview uchun
                supports_streaming=True,
            )

    except Exception as e:

        logger.exception(
            "Video process xatosi"
        )

        await update.message.reply_text(
            "❌ Yuklashda xatolik.\n\n"
            f"{str(e)[:1000]}"
        )

    finally:

        if status_message:

            try:

                await status_message.delete()

            except:
                pass

        shutil.rmtree(
            workdir,
            ignore_errors=True
        )


# =========================================================
# START
# =========================================================

async def start(
    update,
    context
):

    text = (
        "👋 Assalomu alaykum!\n\n"
        "Media yuklash uchun havolani yuboring.\n\n"
        "Qo‘shiq qidirish uchun "
        "qo‘shiqchi yoki qo‘shiq nomini yozing.\n\n"
        "🎤 Audio yoki voice yuborsangiz, "
        "Shazam orqali aniqlayman."
    )

    await update.message.reply_text(
        text
    )


# =========================================================
# TEXT HANDLER
# =========================================================

async def handle_text(
    update,
    context
):

    if not update.message:
        return

    text = (
        update.message.text or ""
    ).strip()

    if not text:
        return

    # URL
    url_match = re.search(
        r"https?://\S+",
        text
    )

    if url_match:

        url = (
            url_match.group(0)
            .rstrip(").,]")
        )

        if not is_supported_url(url):

            await update.message.reply_text(
                "❌ Bu havola qo‘llab-quvvatlanmaydi."
            )

            return

        # YouTube uchun tugmalar
        if is_youtube(url):

            context.user_data[
                "youtube_url"
            ] = url

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎬 VIDEO",
                        callback_data=
                            "yt:video"
                    ),
                    InlineKeyboardButton(
                        "🎵 MP3",
                        callback_data=
                            "yt:mp3"
                    ),
                ]
            ])

            await update.message.reply_text(
                "Kerakli formatni tanlang:",
                reply_markup=keyboard
            )

            return

        await process_video(
            update,
            context,
            url,
            audio=False
        )

        return

    # =============================================
    # QO'SHIQ QIDIRISH
    # =============================================

    search_message = (
        await update.message.reply_text(
            "🔎 Qidirilmoqda..."
        )
    )

    try:

        yt_task = asyncio.to_thread(
            youtube_search_sync,
            text
        )

        google_task = asyncio.to_thread(
            google_search_sync,
            text
        )

        yt_results, google_results = (
            await asyncio.gather(
                yt_task,
                google_task,
                return_exceptions=True
            )
        )

        if isinstance(
            yt_results,
            Exception
        ):

            yt_results = []

        if isinstance(
            google_results,
            Exception
        ):

            google_results = []

        # Asosiy natijalar YouTube.
        # Google natijalari fallback sifatida.
        results = yt_results

        if not results:

            results = [
                {
                    "title":
                        x["title"],

                    "url":
                        x["url"],

                    "id":
                        None,

                    "google":
                        True,
                }

                for x in google_results
                if x.get("url")
            ]

        results = results[:50]

        if not results:

            await search_message.edit_text(
                "❌ Hech qanday natija topilmadi."
            )

            return

        user_id = (
            update.effective_user.id
        )

        USER_SEARCH_DATA[
            user_id
        ] = {

            "query":
                text,

            "results":
                results,
        }

        keyboard = build_search_keyboard(
            user_id,
            0
        )

        await search_message.edit_text(
            "🎵 50 tagacha natija topildi.\n"
            "Kerakli qo‘shiqni tanlang:",
            reply_markup=keyboard
        )

    except Exception as e:

        logger.exception(
            "Search xatosi"
        )

        await search_message.edit_text(
            "❌ Qidirishda xatolik."
        )


# =========================================================
# AUDIO / VOICE
# =========================================================

async def handle_audio(
    update,
    context
):

    message = update.message

    if not message:
        return

    status = await message.reply_text(
        "🎧 Ovoz aniqlanmoqda..."
    )

    workdir = Path(
        tempfile.mkdtemp(
            prefix="shazam_",
            dir=DOWNLOAD_DIR
        )
    )

    try:

        if message.voice:

            telegram_file = await context.bot.get_file(
                message.voice.file_id
            )

            input_file = (
                workdir / "voice.ogg"
            )

            await telegram_file.download_to_drive(
                custom_path=str(input_file)
            )

        elif message.audio:

            telegram_file = await context.bot.get_file(
                message.audio.file_id
            )

            ext = ".mp3"

            if message.audio.file_name:

                ext = Path(
                    message.audio.file_name
                ).suffix or ".mp3"

            input_file = (
                workdir /
                f"audio{ext}"
            )

            await telegram_file.download_to_drive(
                custom_path=str(input_file)
            )

        else:
            return

        result = await recognize_shazam(
            input_file
        )

        if not result:

            await status.edit_text(
                "❌ Qo‘shiq aniqlanmadi."
            )

            return

        artist = result["artist"]
        title = result["title"]

        context.user_data[
            "shazam_query"
        ] = f"{artist} {title}"

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
            "🎵 Qo‘shiq aniqlandi:\n\n"
            f"👤 {artist}\n"
            f"🎶 {title}",
            reply_markup=keyboard
        )

    except Exception as e:

        logger.exception(
            "Shazam xatosi"
        )

        await status.edit_text(
            "❌ Audio aniqlashda xatolik."
        )

    finally:

        shutil.rmtree(
            workdir,
            ignore_errors=True
        )


# =========================================================
# CALLBACK
# =========================================================

async def callback_handler(
    update,
    context
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    data = query.data or ""

    user_id = (
        query.from_user.id
    )

    # =============================================
    # NOOP
    # =============================================

    if data == "noop":
        return

    # =============================================
    # YOUTUBE VIDEO
    # =============================================

    if data == "yt:video":

        url = context.user_data.get(
            "youtube_url"
        )

        if not url:

            await query.message.reply_text(
                "❌ Havola topilmadi."
            )

            return

        fake_update = update

        await process_callback_video(
            fake_update,
            context,
            url,
            audio=False
        )

        return

    # =============================================
    # YOUTUBE MP3
    # =============================================

    if data == "yt:mp3":

        url = context.user_data.get(
            "youtube_url"
        )

        if not url:

            await query.message.reply_text(
                "❌ Havola topilmadi."
            )

            return

        await process_callback_video(
            update,
            context,
            url,
            audio=True
        )

        return

    # =============================================
    # SEARCH PAGE
    # =============================================

    if data.startswith(
        "page:"
    ):

        try:

            page = int(
                data.split(
                    ":",
                    1
                )[1]
            )

        except:

            return

        keyboard = build_search_keyboard(
            user_id,
            page
        )

        if keyboard:

            try:

                await query.message.edit_reply_markup(
                    reply_markup=keyboard
                )

            except:
                pass

        return

    # =============================================
    # SONG
    # =============================================

    if data.startswith(
        "song:"
    ):

        try:

            index = int(
                data.split(
                    ":",
                    1
                )[1]
            )

        except:

            return

        user_data = USER_SEARCH_DATA.get(
            user_id
        )

        if not user_data:
            return

        results = user_data[
            "results"
        ]

        if (
            index < 0
            or index >= len(results)
        ):
            return

        selected = results[index]

        url = selected.get(
            "url"
        )

        if not url:
            return

        # Google natija bo'lsa:
        # to'g'ridan-to'g'ri yt-dlp
        # bilan urinib ko'ramiz.
        await process_callback_video(
            update,
            context,
            url,
            audio=True
        )

        return

    # =============================================
    # SHAZAM DOWNLOAD
    # =============================================

    if data == "shazam:download":

        search_query = context.user_data.get(
            "shazam_query"
        )

        if not search_query:

            await query.message.reply_text(
                "❌ Qo‘shiq ma'lumoti topilmadi."
            )

            return

        status = await query.message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        try:

            results = await asyncio.to_thread(
                youtube_search_sync,
                search_query
            )

            if not results:

                await status.edit_text(
                    "❌ Qo‘shiq topilmadi."
                )

                return

            url = results[0]["url"]

            workdir = Path(
                tempfile.mkdtemp(
                    prefix="song_",
                    dir=DOWNLOAD_DIR
                )
            )

            try:

                file_path = await asyncio.to_thread(
                    download_audio_sync,
                    url,
                    workdir
                )

                with open(
                    file_path,
                    "rb"
                ) as f:

                    await query.message.reply_audio(
                        audio=InputFile(
                            f,
                            filename=
                                file_path.name
                        ),
                        caption=CAPTION
                    )

                await status.delete()

            finally:

                shutil.rmtree(
                    workdir,
                    ignore_errors=True
                )

        except Exception as e:

            logger.exception(
                "Shazam MP3 xatosi"
            )

            await status.edit_text(
                "❌ MP3 yuklashda xatolik.\n"
                f"{str(e)[:500]}"
            )

        return


# =========================================================
# CALLBACK VIDEO
# =========================================================

async def process_callback_video(
    update,
    context,
    url,
    audio=False
):

    message = update.callback_query.message

    workdir = Path(
        tempfile.mkdtemp(
            prefix="callback_",
            dir=DOWNLOAD_DIR
        )
    )

    status = None

    try:

        status = await message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        if audio:

            file_path = await asyncio.to_thread(
                download_audio_sync,
                url,
                workdir
            )

            with open(
                file_path,
                "rb"
            ) as f:

                await message.reply_audio(
                    audio=InputFile(
                        f,
                        filename=
                            file_path.name
                    ),
                    caption=CAPTION
                )

        else:

            if is_ok(url):

                source = await asyncio.to_thread(
                    download_ok_video_sync,
                    url,
                    workdir
                )

            else:

                source = await asyncio.to_thread(
                    download_video_sync,
                    url,
                    workdir
                )

            final_file = (
                workdir /
                "final.mp4"
            )

            await asyncio.to_thread(
                compress_video_sync,
                source,
                final_file
            )

            if (
                final_file.stat().st_size
                > TARGET_SIZE
            ):

                raise RuntimeError(
                    "Video 14 MiB dan katta."
                )

            with open(
                final_file,
                "rb"
            ) as f:

                await message.reply_video(
                    video=InputFile(
                        f,
                        filename="video.mp4"
                    ),
                    caption=CAPTION,
                    supports_streaming=True
                )

    except Exception as e:

        logger.exception(
            "Callback video xatosi"
        )

        await message.reply_text(
            "❌ Yuklashda xatolik.\n\n"
            f"{str(e)[:1000]}"
        )

    finally:

        if status:

            try:
                await status.delete()
            except:
                pass

        shutil.rmtree(
            workdir,
            ignore_errors=True
        )


# =========================================================
# ERROR
# =========================================================

async def error_handler(
    update,
    context
):

    logger.exception(
        "Telegram bot xatosi:",
        exc_info=context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN Railway Variables "
            "ichida topilmadi."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO,
            handle_audio
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_text
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "BOT ISHLAYAPTI"
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
