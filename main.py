import os
import re
import base64
import asyncio
import logging
import tempfile
import shutil
import subprocess
import uuid
import time
from pathlib import Path

import requests
import yt_dlp
from shazamio import Shazam

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    FSInputFile,
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
# SOZLAMALAR
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

TARGET_SIZE = 14 * 1024 * 1024

MAX_SOURCE_SIZE = 1024 * 1024 * 1024

DOWNLOAD_DIR = Path("/tmp/yuklatgbot")

DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

FFMPEG_LOCATION = os.getenv(
    "FFMPEG_LOCATION",
    "/usr/bin",
)

GOOGLE_API_KEY = os.getenv(
    "GOOGLE_API_KEY"
)

GOOGLE_CX = os.getenv(
    "GOOGLE_CX"
)

YOUTUBE_COOKIES_B64 = os.getenv(
    "YOUTUBE_COOKIES_B64"
)

YOUTUBE_COOKIES = os.getenv(
    "YOUTUBE_COOKIES"
)

CAPTION = (
    "@yuklatgbot orqali yuklab olindi"
)

PAGE_SIZE = 10

MAX_PAGES = 5

POT_PROVIDER_URL = (
    "http://127.0.0.1:4416"
)


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger(__name__)


# =========================================================
# GLOBAL DATA
# =========================================================

VIDEO_JOBS = {}

USER_SEARCH_DATA = {}

USER_YOUTUBE_URL = {}


# =========================================================
# YOUTUBE COOKIES
# =========================================================

def prepare_youtube_cookies():

    try:

        if YOUTUBE_COOKIES_B64:

            path = (
                DOWNLOAD_DIR
                / "youtube_cookies.txt"
            )

            path.write_bytes(
                base64.b64decode(
                    YOUTUBE_COOKIES_B64
                )
            )

            return str(path)

        if YOUTUBE_COOKIES:

            path = (
                DOWNLOAD_DIR
                / "youtube_cookies.txt"
            )

            path.write_text(
                YOUTUBE_COOKIES,
                encoding="utf-8",
            )

            return str(path)

    except Exception as e:

        logger.error(
            "YouTube cookies xatosi: %s",
            e,
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
            re.I,
        )
    )


def is_instagram(url):

    return (
        "instagram.com"
        in url.lower()
    )


def is_tiktok(url):

    return (
        "tiktok.com"
        in url.lower()
    )


def is_facebook(url):

    u = url.lower()

    return (
        "facebook.com" in u
        or "fb.watch" in u
    )


def is_ok(url):

    u = url.lower()

    return (
        "ok.ru" in u
        or "odnoklassniki.ru" in u
    )


def is_supported_url(url):

    u = url.lower()

    return any([
        is_youtube(url),
        is_instagram(url),
        is_tiktok(url),
        is_facebook(url),
        is_ok(url),
        "pinterest." in u,
        "snapchat." in u,
        "likee." in u,
        "threads." in u,
    ])


# =========================================================
# YT-DLP OPTIONS
# =========================================================

def base_ydl_options(url=None):

    options = {

        "quiet": True,

        "no_warnings": True,

        "noplaylist": True,

        "retries": 5,

        "fragment_retries": 5,

        "socket_timeout": 45,

        "concurrent_fragment_downloads": 8,

        "nocheckcertificate": True,

        "ffmpeg_location":
            FFMPEG_LOCATION,

        "geo_bypass": True,

        "http_headers": {

            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/138.0.0.0 "
                "Safari/537.36"
            ),

            "Accept-Language":
                "en-US,en;q=0.9",
        },
    }


    # -----------------------------------------------------
    # YOUTUBE
    # -----------------------------------------------------

    if url and is_youtube(url):

        cookie_file = (
            prepare_youtube_cookies()
        )

        if cookie_file:

            options["cookiefile"] = (
                cookie_file
            )

        options["extractor_args"] = {

            "youtubepot-bgutilhttp": {

                "base_url":
                    POT_PROVIDER_URL,
            }
        }

        options["remote_components"] = (
            "ejs:github"
        )


    return options


# =========================================================
# HAQIQIY VIDEO FAYLNI TEKSHIRISH
# =========================================================

def is_real_video_file(path):

    if not path.is_file():
        return False

    if path.suffix.lower() in [

        ".part",
        ".ytdl",
        ".temp",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".json",
        ".txt",
        ".m4a",
        ".mp3",
        ".opus",
        ".webm.part",
    ]:

        return False


    command = [

        "ffprobe",

        "-v",
        "error",

        "-select_streams",
        "v:0",

        "-show_entries",
        "stream=codec_type,width,height",

        "-of",
        "csv=p=0",

        str(path),
    ]


    try:

        result = subprocess.run(

            command,

            capture_output=True,

            text=True,

            timeout=30,
        )


        if result.returncode != 0:
            return False


        value = (
            result.stdout.strip()
        )


        if not value:
            return False


        parts = value.split(",")


        if len(parts) < 3:
            return False


        codec_type = parts[0]

        width = int(parts[1])

        height = int(parts[2])


        return (

            codec_type == "video"

            and

            width > 0

            and

            height > 0
        )


    except Exception:

        return False


# =========================================================
# VIDEO FILE TOPISH
# =========================================================

def find_downloaded_file(workdir):

    candidates = []


    for path in workdir.iterdir():

        if not path.is_file():
            continue

        if is_real_video_file(path):

            candidates.append(path)


    if not candidates:

        logger.error(
            "Haqiqiy video fayl topilmadi. "
            "Workdir: %s",
            workdir,
        )

        for p in workdir.iterdir():

            try:

                logger.info(
                    "Topilgan fayl: %s | %.2f MiB",
                    p.name,
                    p.stat().st_size
                    / 1024
                    / 1024,
                )

            except Exception:
                pass

        return None


    # MP4 faylni afzal ko'ramiz

    mp4_files = [

        p for p in candidates

        if p.suffix.lower() == ".mp4"
    ]


    if mp4_files:

        candidates = mp4_files


    # Eng katta haqiqiy video fayl

    candidates.sort(

        key=lambda x:
            x.stat().st_size,

        reverse=True,
    )


    selected = candidates[0]


    width, height = (
        get_video_dimensions(
            selected
        )
    )


    logger.info(

        "VIDEO TANLANDI: %s | %sx%s | %.2f MiB",

        selected.name,

        width,

        height,

        selected.stat().st_size
        / 1024
        / 1024,
    )


    return selected


# =========================================================
# VIDEO YUKLASH
# =========================================================

def download_video_sync(

    url,

    workdir,
):

    output = str(

        workdir
        / "%(title).150s.%(ext)s"
    )


    options = base_ydl_options(
        url
    )


    options.update({

        "outtmpl":
            output,

        "format":
            "bestvideo*+bestaudio/best",

        "merge_output_format":
            "mp4",

        "max_filesize":
            MAX_SOURCE_SIZE,

        "keepvideo":
            False,
    })


    logger.info(
        "Video yuklash boshlandi: %s",
        url,
    )


    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        ydl.download([
            url
        ])


    path = find_downloaded_file(
        workdir
    )


    if not path:

        raise RuntimeError(
            "Yuklangan faylda "
            "haqiqiy video topilmadi."
        )


    size = path.stat().st_size


    if size > MAX_SOURCE_SIZE:

        raise RuntimeError(
            "Manba video 1 GB dan katta."
        )


    width, height = (
        get_video_dimensions(path)
    )


    if not width or not height:

        raise RuntimeError(
            "Yuklangan video "
            "o'lchamini aniqlab bo'lmadi."
        )


    logger.info(

        "SOURCE: %s | %sx%s | %.2f MiB",

        path.name,

        width,

        height,

        size
        / 1024
        / 1024,
    )


    return path


# =========================================================
# OK.RU
# =========================================================

def download_ok_video_sync(

    url,

    workdir,
):

    output = str(

        workdir
        / "%(title).150s.%(ext)s"
    )


    options = base_ydl_options(
        url
    )


    options.update({

        "outtmpl":
            output,

        "format":
            "best[ext=mp4]/best",

        "http_headers": {

            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/138.0.0.0 "
                "Safari/537.36"
            ),

            "Accept": "*/*",

            "Accept-Language":
                "en-US,en;q=0.9",
        },
    })


    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        ydl.download([
            url
        ])


    path = find_downloaded_file(
        workdir
    )


    if not path:

        raise RuntimeError(
            "OK.ru video topilmadi."
        )


    return path


# =========================================================
# AUDIO YUKLASH
# =========================================================

def download_audio_sync(

    url,

    workdir,
):

    output = str(

        workdir
        / "%(title).150s.%(ext)s"
    )


    options = base_ydl_options(
        url
    )


    options.update({

        "outtmpl":
            output,

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


    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        ydl.download([
            url
        ])


    mp3_files = list(

        workdir.glob(
            "*.mp3"
        )
    )


    if not mp3_files:

        raise RuntimeError(
            "MP3 fayli topilmadi."
        )


    mp3_files.sort(

        key=lambda x:
            x.stat().st_mtime,

        reverse=True,
    )


    return mp3_files[0]


# =========================================================
# VIDEO DIMENSION
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
        "csv=p=0:s=x",

        str(file_path),
    ]


    try:

        result = subprocess.run(

            command,

            capture_output=True,

            text=True,

            timeout=30,
        )


        if result.returncode != 0:

            logger.error(

                "ffprobe dimension xatosi: %s",

                result.stderr[-1000:],
            )

            return None, None


        value = (
            result.stdout.strip()
        )


        if "x" not in value:

            return None, None


        width, height = (
            value.split("x", 1)
        )


        width = int(width)

        height = int(height)


        if width <= 0 or height <= 0:

            return None, None


        return width, height


    except Exception as e:

        logger.error(

            "Dimension aniqlash xatosi: %s",

            e,
        )

        return None, None


# =========================================================
# VIDEO DURATION
# =========================================================

def get_duration(
    file_path
):

    command = [

        "ffprobe",

        "-v",
        "error",

        "-show_entries",
        "format=duration",

        "-of",
        "default="
        "noprint_wrappers=1:"
        "nokey=1",

        str(file_path),
    ]


    try:

        result = subprocess.run(

            command,

            capture_output=True,

            text=True,

            timeout=30,
        )


        value = (
            result.stdout.strip()
        )


        duration = float(
            value
        )


        if duration <= 0:

            return 0.0


        return duration


    except Exception:

        return 0.0


# =========================================================
# AUDIO BORLIGINI
# =========================================================

def has_audio_stream(
    file_path
):

    command = [

        "ffprobe",

        "-v",
        "error",

        "-select_streams",
        "a:0",

        "-show_entries",
        "stream=index",

        "-of",
        "csv=p=0",

        str(file_path),
    ]


    try:

        result = subprocess.run(

            command,

            capture_output=True,

            text=True,

            timeout=30,
        )


        return bool(
            result.stdout.strip()
        )

    except Exception:

        return False


# =========================================================
# VIDEO ENCODE
# =========================================================

def encode_video(

    input_file,

    output_file,

    video_bitrate,

    audio_bitrate,
):

    width, height = (
        get_video_dimensions(
            input_file
        )
    )


    if not width or not height:

        raise RuntimeError(
            "Encode uchun video "
            "o'lchami topilmadi."
        )


    audio_exists = (
        has_audio_stream(
            input_file
        )
    )


    logger.info(

        "ENCODE: %s | %sx%s | audio=%s | V=%s | A=%s",

        input_file.name,

        width,

        height,

        audio_exists,

        video_bitrate,

        audio_bitrate,
    )


    command = [

        "ffmpeg",

        "-y",

        "-i",
        str(input_file),

        "-map",
        "0:v:0",

        "-c:v",
        "libx264",

        "-preset",
        "faster",

        "-b:v",
        video_bitrate,

        "-maxrate",
        video_bitrate,

        "-bufsize",
        video_bitrate,
    ]


    # -----------------------------------------------------
    # DIMENSION O'ZGARMAYDI
    # -----------------------------------------------------

    if (

        width % 2 == 0

        and

        height % 2 == 0

    ):

        command += [

            "-pix_fmt",

            "yuv420p",
        ]

    else:

        # Odd o'lchamni o'zgartirmaslik uchun

        command += [

            "-pix_fmt",

            "yuv444p",
        ]


    # -----------------------------------------------------
    # AUDIO
    # -----------------------------------------------------

    if audio_exists:

        command += [

            "-map",
            "0:a:0?",

            "-c:a",
            "aac",

            "-b:a",
            audio_bitrate,

            "-ac",
            "2",

            "-ar",
            "44100",
        ]

    else:

        command += [
            "-an"
        ]


    command += [

        "-sn",

        "-dn",

        "-movflags",
        "+faststart",

        str(output_file),
    ]


    logger.info(

        "FFmpeg ishga tushmoqda..."
    )


    result = subprocess.run(

        command,

        stdout=subprocess.DEVNULL,

        stderr=subprocess.PIPE,

        text=True,

        timeout=1800,
    )


    if result.returncode != 0:

        error_text = (
            result.stderr[-5000:]
        )


        logger.error(

            "FFmpeg FAILED:\n%s",

            error_text,
        )


        raise RuntimeError(

            "FFmpeg xatosi:\n"
            + error_text
        )


    if not output_file.exists():

        raise RuntimeError(
            "FFmpeg chiqish faylini "
            "yaratmadi."
        )


    if output_file.stat().st_size <= 0:

        raise RuntimeError(
            "FFmpeg 0 hajmli fayl yaratdi."
        )


    # -----------------------------------------------------
    # O'LCHAMNI TEKSHIRISH
    # -----------------------------------------------------

    new_width, new_height = (
        get_video_dimensions(
            output_file
        )
    )


    if (

        new_width != width

        or

        new_height != height

    ):

        output_file.unlink(
            missing_ok=True
        )


        raise RuntimeError(

            "Video o'lchami "
            "o'zgardi: "

            f"{width}x{height}"

            " -> "

            f"{new_width}x{new_height}"
        )


# =========================================================
# 14 MiB GACHA SIQISH
# =========================================================

def compress_video_sync(

    input_file,

    output_file,
):

    width, height = (
        get_video_dimensions(
            input_file
        )
    )


    if not width or not height:

        raise RuntimeError(

            "Video o'lchamini "
            "aniqlab bo'lmadi."
        )


    duration = get_duration(
        input_file
    )


    if duration <= 0:

        raise RuntimeError(

            "Video davomiyligini "
            "aniqlab bo'lmadi."
        )


    audio_exists = (
        has_audio_stream(
            input_file
        )
    )


    logger.info(

        "COMPRESS START: %sx%s | %.2f sec | audio=%s",

        width,

        height,

        duration,

        audio_exists,
    )


    # -----------------------------------------------------
    # 13.0 MiB target
    # -----------------------------------------------------
    # Telegram 14 MiB chegarasidan zaxira qoldiramiz.

    target_bits = (

        13.0

        * 1024
        * 1024

        * 8
    )


    total_bitrate = int(

        target_bits
        / duration
    )


    # Juda katta bitrate bermaymiz.

    total_bitrate = max(

        40_000,

        min(
            total_bitrate,
            2_500_000,
        ),
    )


    # -----------------------------------------------------
    # BITRATE BOSQICHLARI
    # -----------------------------------------------------

    multipliers = [

        1.00,
        0.88,
        0.78,
        0.69,
        0.61,
        0.54,
        0.48,
        0.42,
        0.37,
        0.32,
        0.28,
        0.24,
        0.20,
        0.17,
        0.14,
        0.12,
        0.10,
        0.08,
        0.065,
        0.050,
        0.040,
        0.030,
        0.022,
        0.016,
        0.012,
        0.009,
        0.007,
        0.005,
    ]


    last_size = None


    for number, multiplier in enumerate(

        multipliers,

        start=1,
    ):

        current_total = int(

            total_bitrate
            * multiplier
        )


        if audio_exists:

            # Audio uchun kamida 24k

            audio_bitrate_value = min(

                96_000,

                max(
                    24_000,

                    current_total // 5,
                ),
            )


            video_bitrate_value = max(

                8_000,

                current_total
                - audio_bitrate_value,
            )

        else:

            audio_bitrate_value = 0

            video_bitrate_value = max(

                8_000,

                current_total,
            )


        candidate = (

            output_file.parent
            / f"encode_{number}.mp4"
        )


        candidate.unlink(
            missing_ok=True
        )


        try:

            encode_video(

                input_file,

                candidate,

                f"{video_bitrate_value // 1000}k",

                (
                    f"{audio_bitrate_value // 1000}k"

                    if audio_exists

                    else "0k"
                ),
            )


        except Exception as e:

            logger.error(

                "FFmpeg %s-urinish xatosi: %s",

                number,

                e,
            )


            candidate.unlink(
                missing_ok=True
            )

            continue


        if not candidate.exists():
            continue


        last_size = (
            candidate.stat().st_size
        )


        logger.info(

            "ENCODE %s | %.2f MiB | V=%dk | A=%dk",

            number,

            last_size
            / 1024
            / 1024,

            video_bitrate_value
            // 1000,

            audio_bitrate_value
            // 1000,
        )


        # -------------------------------------------------
        # TARGETGA YETDI
        # -------------------------------------------------

        if last_size <= TARGET_SIZE:

            output_file.unlink(
                missing_ok=True
            )


            candidate.rename(
                output_file
            )


            # Final dimension

            new_width, new_height = (
                get_video_dimensions(
                    output_file
                )
            )


            if (

                new_width != width

                or

                new_height != height

            ):

                output_file.unlink(
                    missing_ok=True
                )


                raise RuntimeError(

                    "Video o'lchami "
                    "o'zgardi: "

                    f"{width}x{height}"

                    " -> "

                    f"{new_width}x{new_height}"
                )


            logger.info(

                "FINAL VIDEO: %.2f MiB | %sx%s",

                output_file.stat().st_size
                / 1024
                / 1024,

                new_width,

                new_height,
            )


            return output_file


        candidate.unlink(
            missing_ok=True
        )


    # -----------------------------------------------------
    # SIQIB BO'LMADI
    # -----------------------------------------------------

    if last_size is not None:

        raise RuntimeError(

            "Videoni 14 MiB gacha "
            "siqib bo'lmadi.\n"

            f"Oxirgi hajm: "

            f"{last_size / 1024 / 1024:.2f} MiB"
        )


    raise RuntimeError(

        "Video qayta kodlanmadi."
    )


# =========================================================
# VIDEO DAN MP3
# =========================================================

def extract_mp3_from_video_sync(

    video_file,

    output_mp3,
):

    command = [

        "ffmpeg",

        "-y",

        "-i",
        str(video_file),

        "-vn",

        "-c:a",
        "libmp3lame",

        "-b:a",
        "128k",

        "-ar",
        "44100",

        "-ac",
        "2",

        str(output_mp3),
    ]


    result = subprocess.run(

        command,

        stdout=subprocess.DEVNULL,

        stderr=subprocess.PIPE,

        text=True,

        timeout=600,
    )


    if result.returncode != 0:

        raise RuntimeError(

            "MP3 yaratishda "
            "FFmpeg xatosi:\n"

            + result.stderr[-2000:]
        )


    if not output_mp3.exists():

        raise RuntimeError(
            "MP3 yaratilmadi."
        )


    if output_mp3.stat().st_size <= 0:

        raise RuntimeError(
            "MP3 0 hajmda yaratildi."
        )


    return output_mp3


# =========================================================
# VIDEO JOB
# =========================================================

def create_video_job(

    user_id,

    workdir,

    mp3_path,
):

    job_id = (

        uuid.uuid4()
        .hex[:16]
    )


    VIDEO_JOBS[job_id] = {

        "user_id":
            user_id,

        "workdir":
            str(workdir),

        "mp3_path":
            str(mp3_path),

        "created":
            time.time(),
    }


    return job_id


# =========================================================
# VIDEO YUKLAB YUBORISH
# =========================================================

async def download_and_send_video(

    message,

    url,

    user_id,
):

    workdir = Path(

        tempfile.mkdtemp(

            prefix=
                f"{user_id}_",

            dir=
                DOWNLOAD_DIR,
        )
    )


    status = None

    keep_workdir = False


    try:

        status = (

            await message.reply_text(

                "⏳ Yuklanmoqda..."
            )
        )


        # -------------------------------------------------
        # DOWNLOAD
        # -------------------------------------------------

        if is_ok(url):

            source = (

                await asyncio.to_thread(

                    download_ok_video_sync,

                    url,

                    workdir,
                )
            )

        else:

            source = (

                await asyncio.to_thread(

                    download_video_sync,

                    url,

                    workdir,
                )
            )


        # -------------------------------------------------
        # SOURCE TEKSHIRISH
        # -------------------------------------------------

        source_width, source_height = (
            get_video_dimensions(
                source
            )
        )


        if not source_width or not source_height:

            raise RuntimeError(

                "Yuklangan faylda "
                "video stream topilmadi."
            )


        logger.info(

            "SOURCE VIDEO: %s | %sx%s",

            source.name,

            source_width,

            source_height,
        )


        # -------------------------------------------------
        # COMPRESS
        # -------------------------------------------------

        final_video = (

            workdir
            / "final.mp4"
        )


        await asyncio.to_thread(

            compress_video_sync,

            source,

            final_video,
        )


        # -------------------------------------------------
        # FINAL TEKSHIRISH
        # -------------------------------------------------

        final_width, final_height = (
            get_video_dimensions(
                final_video
            )
        )


        if (

            final_width != source_width

            or

            final_height != source_height

        ):

            raise RuntimeError(

                "Final video o'lchami "
                "o'zgardi: "

                f"{source_width}x{source_height}"

                " -> "

                f"{final_width}x{final_height}"
            )


        final_size = (
            final_video.stat().st_size
        )


        if final_size > TARGET_SIZE:

            raise RuntimeError(

                "Final video 14 MiB dan "
                "katta bo'lib qoldi: "

                f"{final_size / 1024 / 1024:.2f} MiB"
            )


        # -------------------------------------------------
        # MP3
        # -------------------------------------------------

        mp3_path = None


        if has_audio_stream(
            source
        ):

            try:

                mp3_path = (

                    workdir
                    / "audio.mp3"
                )


                await asyncio.to_thread(

                    extract_mp3_from_video_sync,

                    source,

                    mp3_path,
                )


            except Exception:

                logger.exception(

                    "Video MP3 yaratishda xato"
                )

                mp3_path = None


        # -------------------------------------------------
        # BUTTON
        # -------------------------------------------------

        keyboard = None


        if (

            mp3_path

            and

            mp3_path.exists()

        ):

            job_id = create_video_job(

                user_id,

                workdir,

                mp3_path,
            )


            keyboard = (
                InlineKeyboardMarkup([

                    [

                        InlineKeyboardButton(

                            "🎵 MP3 yuklash",

                            callback_data=
                                f"media:{job_id}",
                        )

                    ]

                ])
            )


            keep_workdir = True


        # -------------------------------------------------
        # SEND VIDEO
        # -------------------------------------------------

        await message.reply_video(

            video=FSInputFile(

                str(final_video),

                filename="video.mp4",
            ),

            caption=CAPTION,

            supports_streaming=True,

            reply_markup=keyboard,
        )


        logger.info(

            "VIDEO YUBORILDI: %.2f MiB",

            final_size
            / 1024
            / 1024,
        )


    except Exception as e:

        logger.exception(
            "Video xatosi"
        )


        await message.reply_text(

            "❌ Yuklashda xatolik.\n\n"

            f"{str(e)[:1800]}"
        )


    finally:

        if status:

            try:

                await status.delete()

            except Exception:

                pass


        if not keep_workdir:

            shutil.rmtree(

                workdir,

                ignore_errors=True,
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


    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        data = ydl.extract_info(

            f"ytsearch50:{query}",

            download=False,
        )


    results = []


    if not data:

        return results


    for item in data.get(
        "entries",
        [],
    ):

        if not item:

            continue


        video_id = item.get(
            "id"
        )


        if not video_id:

            continue


        results.append({

            "title":
                item.get(
                    "title",
                    "Noma'lum",
                ),

            "url":
                (
                    "https://www.youtube.com/watch?v="
                    + video_id
                ),
        })


    return results[:50]


# =========================================================
# GOOGLE
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
        41,

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


        try:

            response = requests.get(

                "https://www.googleapis.com/customsearch/v1",

                params=params,

                timeout=20,
            )


            if response.status_code != 200:

                continue


            data = (
                response.json()
            )


            for item in data.get(
                "items",
                [],
            ):

                if item.get(
                    "link"
                ):

                    results.append({

                        "title":
                            item.get(
                                "title",
                                "Natija",
                            ),

                        "url":
                            item["link"],

                        "google":
                            True,
                    })


        except Exception:

            logger.exception(
                "Google xatosi"
            )


    return results[:50]


# =========================================================
# SEARCH BUTTON
# =========================================================

def build_search_keyboard(

    user_id,

    page,
):

    data = (
        USER_SEARCH_DATA.get(
            user_id
        )
    )


    if not data:

        return None


    results = data[
        "results"
    ]


    start = (
        page
        * PAGE_SIZE
    )


    current = results[

        start:

        start + PAGE_SIZE
    ]


    buttons = []


    for i, item in enumerate(

        current,

        start=start,
    ):

        title = item.get(
            "title",
            "Noma'lum",
        )[:55]


        buttons.append([

            InlineKeyboardButton(

                f"🎵 {i + 1}. {title}",

                callback_data=
                    f"song:{i}",
            )

        ])


    max_page = min(

        MAX_PAGES,

        max(

            1,

            (
                len(results)
                + PAGE_SIZE
                - 1
            )
            // PAGE_SIZE,
        ),
    )


    if max_page > 1:

        nav = []


        if page > 0:

            nav.append(

                InlineKeyboardButton(

                    "⬅️",

                    callback_data=
                        f"page:{page - 1}",
                )
            )


        nav.append(

            InlineKeyboardButton(

                f"{page + 1}/{max_page}",

                callback_data="noop",
            )
        )


        if page < max_page - 1:

            nav.append(

                InlineKeyboardButton(

                    "➡️",

                    callback_data=
                        f"page:{page + 1}",
                )
            )


        buttons.append(nav)


    return InlineKeyboardMarkup(
        buttons
    )


# =========================================================
# START
# =========================================================

async def start(

    update: Update,

    context:
        ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(

        "👋 Assalomu alaykum!\n\n"

        "Media yuklash uchun "
        "havolani yuboring.\n\n"

        "Instagram — post, stories, reels\n"
        "YouTube — video, shorts, audio\n"
        "TikTok — video\n"
        "Facebook — reels/video\n"
        "OK.ru — video\n"
        "Pinterest — rasm/video\n"
        "Snapchat — rasm/video\n"
        "Likee — video\n"
        "Threads — media\n\n"

        "🎵 Qo'shiq qidirish uchun "
        "qo'shiqchi yoki qo'shiq "
        "nomini yozing.\n\n"

        "🎤 Audio yoki voice yuborsangiz, "
        "Shazam orqali aniqlayman."
    )


# =========================================================
# TEXT
# =========================================================

async def handle_text(

    update: Update,

    context:
        ContextTypes.DEFAULT_TYPE,
):

    text = (

        update.message.text
        or ""
    ).strip()


    if not text:

        return


    match = re.search(

        r"https?://\S+",

        text,
    )


    # -----------------------------------------------------
    # URL
    # -----------------------------------------------------

    if match:

        url = (

            match.group(0)
            .rstrip(").,]")
        )


        if not is_supported_url(
            url
        ):

            await update.message.reply_text(

                "❌ Bu havola "
                "qo'llab-quvvatlanmaydi."
            )

            return


        # -------------------------------------------------
        # YOUTUBE
        # -------------------------------------------------

        if is_youtube(url):

            USER_YOUTUBE_URL[
                update.effective_user.id
            ] = url


            await update.message.reply_text(

                "Kerakli formatni tanlang:",

                reply_markup=
                    InlineKeyboardMarkup([

                        [

                            InlineKeyboardButton(

                                "🎬 VIDEO",

                                callback_data=
                                    "yt:video",
                            ),

                            InlineKeyboardButton(

                                "🎵 MP3",

                                callback_data=
                                    "yt:mp3",
                            ),

                        ]

                    ]),
            )

            return


        # -------------------------------------------------
        # BOSHQA SAYTLAR
        # -------------------------------------------------

        await download_and_send_video(

            update.message,

            url,

            update.effective_user.id,
        )

        return


    # -----------------------------------------------------
    # SONG SEARCH
    # -----------------------------------------------------

    status = (

        await update.message.reply_text(

            "🔎 Qidirilmoqda..."
        )
    )


    try:

        yt_task = asyncio.to_thread(

            youtube_search_sync,

            text,
        )


        google_task = asyncio.to_thread(

            google_search_sync,

            text,
        )


        yt_results, google_results = (

            await asyncio.gather(

                yt_task,

                google_task,

                return_exceptions=True,
            )
        )


        if isinstance(
            yt_results,
            Exception,
        ):

            yt_results = []


        if isinstance(
            google_results,
            Exception,
        ):

            google_results = []


        results = (
            yt_results[:50]
        )


        if not results:

            results = (
                google_results[:50]
            )


        if not results:

            await status.edit_text(

                "❌ Natija topilmadi."
            )

            return


        user_id = (
            update.effective_user.id
        )


        USER_SEARCH_DATA[
            user_id
        ] = {

            "results":
                results,

            "page":
                0,
        }


        await status.edit_text(

            "🎵 Natijalar:\n"
            "Kerakli qo'shiqni tanlang:",

            reply_markup=
                build_search_keyboard(

                    user_id,

                    0,
                ),
        )


    except Exception:

        logger.exception(
            "Search xatosi"
        )


        await status.edit_text(

            "❌ Qidirishda xatolik."
        )


# =========================================================
# SHAZAM
# =========================================================

async def handle_audio(

    update,

    context,
):

    message = update.message


    status = (

        await message.reply_text(

            "🎧 Qo'shiq aniqlanmoqda..."
        )
    )


    workdir = Path(

        tempfile.mkdtemp(

            prefix="shazam_",

            dir=DOWNLOAD_DIR,
        )
    )


    try:

        if message.voice:

            tg_file = (

                await context.bot.get_file(

                    message.voice.file_id
                )
            )


            input_file = (

                workdir
                / "voice.ogg"
            )


            await tg_file.download_to_drive(

                custom_path=
                    str(input_file)
            )


        elif message.audio:

            tg_file = (

                await context.bot.get_file(

                    message.audio.file_id
                )
            )


            ext = ".mp3"


            if message.audio.file_name:

                ext = (

                    Path(
                        message.audio.file_name
                    ).suffix

                    or

                    ".mp3"
                )


            input_file = (

                workdir
                / f"audio{ext}"
            )


            await tg_file.download_to_drive(

                custom_path=
                    str(input_file)
            )


        else:

            return


        shazam = Shazam()


        result = await shazam.recognize(

            str(input_file)
        )


        track = result.get(
            "track"
        )


        if not track:

            await status.edit_text(

                "❌ Qo'shiq aniqlanmadi."
            )

            return


        title = track.get(
            "title"
        )


        artist = track.get(
            "subtitle",
            "",
        )


        if not title:

            await status.edit_text(

                "❌ Qo'shiq aniqlanmadi."
            )

            return


        context.user_data[
            "shazam_query"
        ] = (

            f"{artist} {title}"
        )


        await status.edit_text(

            "🎵 Qo'shiq aniqlandi:\n\n"

            f"👤 {artist}\n"

            f"🎶 {title}",

            reply_markup=
                InlineKeyboardMarkup([

                    [

                        InlineKeyboardButton(

                            "🎵 MP3",

                            callback_data=
                                "shazam:download",
                        )

                    ]

                ]),
        )


    except Exception:

        logger.exception(
            "Shazam xatosi"
        )


        await status.edit_text(

            "❌ Audio aniqlashda xatolik."
        )


    finally:

        shutil.rmtree(

            workdir,

            ignore_errors=True,
        )


# =========================================================
# CALLBACK
# =========================================================

async def callback_handler(

    update,

    context,
):

    query = (
        update.callback_query
    )


    await query.answer()


    data = (
        query.data or ""
    )


    user_id = (
        query.from_user.id
    )


    # -----------------------------------------------------
    # NOOP
    # -----------------------------------------------------

    if data == "noop":

        return


    # -----------------------------------------------------
    # YOUTUBE VIDEO
    # -----------------------------------------------------

    if data == "yt:video":

        url = (
            USER_YOUTUBE_URL.get(
                user_id
            )
        )


        if not url:

            await query.message.reply_text(

                "❌ Havola topilmadi."
            )

            return


        await download_and_send_video(

            query.message,

            url,

            user_id,
        )

        return


    # -----------------------------------------------------
    # YOUTUBE MP3
    # -----------------------------------------------------

    if data == "yt:mp3":

        url = (
            USER_YOUTUBE_URL.get(
                user_id
            )
        )


        if not url:

            await query.message.reply_text(

                "❌ Havola topilmadi."
            )

            return


        workdir = Path(

            tempfile.mkdtemp(

                prefix="mp3_",

                dir=DOWNLOAD_DIR,
            )
        )


        status = (

            await query.message.reply_text(

                "⏳ Yuklanmoqda..."
            )
        )


        try:

            p = await asyncio.to_thread(

                download_audio_sync,

                url,

                workdir,
            )


            await query.message.reply_audio(

                audio=FSInputFile(

                    str(p),

                    filename="audio.mp3",
                ),

                caption=CAPTION,
            )


        except Exception as e:

            logger.exception(

                "YouTube MP3 xatosi"
            )


            await query.message.reply_text(

                "❌ MP3 yuklashda xatolik.\n"

                f"{str(e)[:1200]}"
            )


        finally:

            try:

                await status.delete()

            except Exception:

                pass


            shutil.rmtree(

                workdir,

                ignore_errors=True,
            )


        return


    # -----------------------------------------------------
    # VIDEO OSTIDAGI MP3
    # -----------------------------------------------------

    if data.startswith(
        "media:"
    ):

        job_id = data.split(
            ":",
            1,
        )[1]


        job = VIDEO_JOBS.get(
            job_id
        )


        if not job:

            await query.message.reply_text(

                "❌ MP3 fayli muddati "
                "tugagan. Videoni qayta "
                "yuboring."
            )

            return


        if job.get(
            "user_id"
        ) != user_id:

            await query.message.reply_text(

                "❌ Bu fayl sizga "
                "tegishli emas."
            )

            return


        mp3_path = job.get(
            "mp3_path"
        )


        if not mp3_path:

            await query.message.reply_text(

                "❌ Videoda audio "
                "topilmadi."
            )

            return


        mp3_file = Path(
            mp3_path
        )


        if not mp3_file.exists():

            await query.message.reply_text(

                "❌ MP3 fayli endi "
                "mavjud emas. "
                "Videoni qayta yuboring."
            )

            return


        status = (

            await query.message.reply_text(

                "⏳ Yuklanmoqda..."
            )
        )


        try:

            await query.message.reply_audio(

                audio=FSInputFile(

                    str(mp3_file),

                    filename="audio.mp3",
                ),

                caption=CAPTION,
            )


        except Exception:

            logger.exception(

                "MP3 yuborish xatosi"
            )


            await query.message.reply_text(

                "❌ MP3 yuborishda "
                "xatolik."
            )


        finally:

            try:

                await status.delete()

            except Exception:

                pass


            job = VIDEO_JOBS.pop(

                job_id,

                None,
            )


            if job:

                shutil.rmtree(

                    job.get(
                        "workdir",
                        "",
                    ),

                    ignore_errors=True,
                )


        return


    # -----------------------------------------------------
    # PAGE
    # -----------------------------------------------------

    if data.startswith(
        "page:"
    ):

        try:

            page = int(

                data.split(
                    ":",
                    1,
                )[1]
            )

        except Exception:

            return


        saved = (
            USER_SEARCH_DATA.get(
                user_id
            )
        )


        if not saved:

            return


        page = max(

            0,

            min(

                page,

                MAX_PAGES - 1,
            ),
        )


        saved["page"] = page


        keyboard = (
            build_search_keyboard(

                user_id,

                page,
            )
        )


        if keyboard:

            try:

                await query.message.edit_reply_markup(

                    reply_markup=keyboard
                )

            except Exception:

                pass


        return


    # -----------------------------------------------------
    # SONG
    # -----------------------------------------------------

    if data.startswith(
        "song:"
    ):

        try:

            index = int(

                data.split(
                    ":",
                    1,
                )[1]
            )

        except Exception:

            return


        saved = (
            USER_SEARCH_DATA.get(
                user_id
            )
        )


        if not saved:

            re
    
