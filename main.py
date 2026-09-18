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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
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

TARGET_SIZE = 14 * 1024 * 1024       # 14 MiB
MAX_SOURCE_SIZE = 1024 * 1024 * 1024  # 1 GB

DOWNLOAD_DIR = Path("/tmp/yuklatgbot")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

FFMPEG_LOCATION = os.getenv("FFMPEG_LOCATION", "/usr/bin")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")

YOUTUBE_COOKIES_B64 = os.getenv("YOUTUBE_COOKIES_B64")
YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES")

CAPTION = "@yuklatgbot orqali yuklab olindi"

PAGE_SIZE = 10
MAX_PAGES = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Video ostidagi MP3 tugmasi uchun vaqtinchalik ma'lumotlar
VIDEO_JOBS = {}

# Qo'shiq qidiruv natijalari
USER_SEARCH_DATA = {}

# YouTube tanlangan URL
USER_YOUTUBE_URL = {}


# =========================================================
# COOKIES
# =========================================================

def prepare_youtube_cookies():
    try:
        if YOUTUBE_COOKIES_B64:
            p = DOWNLOAD_DIR / "youtube_cookies.txt"
            p.write_bytes(base64.b64decode(YOUTUBE_COOKIES_B64))
            return str(p)

        if YOUTUBE_COOKIES:
            p = DOWNLOAD_DIR / "youtube_cookies.txt"
            p.write_text(YOUTUBE_COOKIES, encoding="utf-8")
            return str(p)

    except Exception as e:
        logger.error("Cookies xatosi: %s", e)

    return None


# =========================================================
# URL ANIQLASH
# =========================================================

def is_youtube(url):
    return bool(re.search(r"(youtube\.com|youtu\.be)", url, re.I))


def is_instagram(url):
    return "instagram.com" in url.lower()


def is_tiktok(url):
    return "tiktok.com" in url.lower()


def is_facebook(url):
    u = url.lower()
    return "facebook.com" in u or "fb.watch" in u


def is_ok(url):
    u = url.lower()
    return "ok.ru" in u or "odnoklassniki.ru" in u


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
# YT-DLP
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

        "ffmpeg_location": FFMPEG_LOCATION,

        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            )
        },
    }

    if url and is_youtube(url):
        cookies = prepare_youtube_cookies()

        if cookies:
            options["cookiefile"] = cookies

        options["extractor_args"] = {
            "youtube": {
                "player_client": [
                    "mweb",
                    "web_embedded",
                    "tv",
                ]
            }
        }

    return options


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
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )

    return files[0]


# =========================================================
# VIDEO YUKLASH
# =========================================================

def download_video_sync(url, workdir):
    output = str(
        workdir / "%(title).150s.%(ext)s"
    )

    options = base_ydl_options(url)

    options.update({
        "outtmpl": output,

        "format": (
            "bestvideo+bestaudio/"
            "best"
        ),

        "merge_output_format": "mp4",
    })

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    p = find_downloaded_file(workdir)

    if not p:
        raise RuntimeError(
            "Video fayli topilmadi."
        )

    if p.stat().st_size > MAX_SOURCE_SIZE:
        raise RuntimeError(
            "Manba video 1 GB dan katta."
        )

    return p


# =========================================================
# OK.RU VIDEO
# =========================================================

def download_ok_video_sync(url, workdir):
    output = str(
        workdir / "%(title).150s.%(ext)s"
    )

    options = base_ydl_options(url)

    options.update({
        "outtmpl": output,

        "format": (
            "best[ext=mp4]/"
            "best"
        ),

        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),

            "Accept": "*/*",

            "Accept-Language": (
                "en-US,en;q=0.9"
            ),
        },
    })

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    p = find_downloaded_file(workdir)

    if not p:
        raise RuntimeError(
            "OK.ru video topilmadi."
        )

    return p


# =========================================================
# MP3 YUKLASH
# =========================================================

def download_audio_sync(url, workdir):
    output = str(
        workdir / "%(title).150s.%(ext)s"
    )

    options = base_ydl_options(url)

    options.update({
        "outtmpl": output,

        "format": "bestaudio/best",

        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
    })

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    mp3 = find_downloaded_file(workdir)

    if not mp3:
        raise RuntimeError(
            "MP3 fayli topilmadi."
        )

    return mp3


# =========================================================
# VIDEO O'LCHAMINI ANIQLASH
# =========================================================

def get_video_dimensions(file_path):
    cmd = [
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
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None, None

    try:
        width, height = (
            result.stdout.strip().split("x")
        )

        return int(width), int(height)

    except Exception:
        return None, None


# =========================================================
# VIDEO DURATION
# =========================================================

def get_duration(file_path):
    cmd = [
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
        cmd,
        capture_output=True,
        text=True,
    )

    try:
        return float(
            result.stdout.strip()
        )
    except Exception:
        return 0.0


# =========================================================
# AUDIO BOR-YO'QLIGINI TEKSHIRISH
# =========================================================

def has_audio_stream(file_path):
    cmd = [
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

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    return bool(
        result.stdout.strip()
    )


# =========================================================
# VIDEO ENCODE
# =========================================================

def encode_video(
    input_file,
    output_file,
    video_bitrate,
    audio_bitrate,
):
    """
    MUHIM:

    Bu yerda SCALE ishlatilmaydi.

    Masalan:
    1920x1080 -> 1920x1080
    1280x720  -> 1280x720
    1080x1920 -> 1080x1920

    O'lcham o'zgarmaydi.

    Faqat bitrate kamayadi va video
    H.264 formatida qayta kodlanadi.
    """

    audio_exists = has_audio_stream(
        input_file
    )

    cmd = [
        "ffmpeg",
        "-y",

        "-i",
        str(input_file),

        "-map",
        "0:v:0",
    ]

    if audio_exists:
        cmd += [
            "-map",
            "0:a:0",
        ]

    cmd += [
        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-b:v",
        video_bitrate,

        "-maxrate",
        video_bitrate,

        "-bufsize",
        video_bitrate,

        "-pix_fmt",
        "yuv420p",
    ]

    if audio_exists:
        cmd += [
            "-c:a",
            "aac",

            "-b:a",
            audio_bitrate,

            "-ac",
            "2",
        ]

    else:
        cmd += [
            "-an",
        ]

    cmd += [
        "-movflags",
        "+faststart",

        str(output_file),
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg xatosi:\n"
            + result.stderr[-2500:]
        )


# =========================================================
# 14 MiB GACHA SIQISH
# =========================================================

def compress_video_sync(
    input_file,
    output_file,
):
    """
    Video HAR DOIM qayta kodlanadi.

    Width/height o'zgarmaydi.

    14 MiB dan kichik bo'lguncha
    video bitrate pasaytiriladi.

    CRF loop ishlatilmaydi.
    """

    width, height = get_video_dimensions(
        input_file
    )

    if not width or not height:
        raise RuntimeError(
            "Video o'lchamini aniqlab bo'lmadi."
        )

    duration = get_duration(
        input_file
    )

    if duration <= 0:
        raise RuntimeError(
            "Video davomiyligini aniqlab bo'lmadi."
        )

    audio_exists = has_audio_stream(
        input_file
    )

    # 14 MiB ichida ozgina zaxira qoldiramiz.
    target_bytes = int(
        TARGET_SIZE * 0.96
    )

    # Umumiy target bitrate.
    total_bps = int(
        (target_bytes * 8) / duration
    )

    if audio_exists:
        audio_candidates = [
            96_000,
            80_000,
            64_000,
            48_000,
            32_000,
        ]
    else:
        audio_candidates = [
            0,
        ]

    # Juda yuqori bitrate bilan boshlamaslik.
    # Har bir keyingi urinish sifatni pasaytiradi.
    multipliers = [
        1.00,
        0.82,
        0.68,
        0.56,
        0.46,
        0.38,
        0.31,
        0.25,
        0.20,
        0.16,
        0.13,
        0.10,
        0.08,
        0.06,
        0.045,
        0.032,
        0.022,
        0.015,
    ]

    last_size = 0

    for attempt, multiplier in enumerate(
        multipliers,
        start=1,
    ):
        if audio_exists:
            audio_bps = audio_candidates[
                min(
                    attempt - 1,
                    len(audio_candidates) - 1,
                )
            ]
        else:
            audio_bps = 0

        video_bps = int(
            (total_bps - audio_bps)
            * multiplier
        )

        # Video bitrate juda past bo'lib ketmasin.
        video_bps = max(
            video_bps,
            20_000,
        )

        video_k = max(
            20,
            video_bps // 1000,
        )

        audio_k = (
            audio_bps // 1000
            if audio_exists
            else 0
        )

        candidate = (
            output_file.parent
            / f"candidate_{attempt}.mp4"
        )

        candidate.unlink(
            missing_ok=True
        )

        logger.info(
            "Encode %s: %sk video, %sk audio",
            attempt,
            video_k,
            audio_k,
        )

        try:
            encode_video(
                input_file,
                candidate,
                f"{video_k}k",
                f"{audio_k}k" if audio_exists else "0k",
            )
        except Exception as e:
            logger.error(
                "Encode urinish %s: %s",
                attempt,
                e,
            )

            candidate.unlink(
                missing_ok=True
            )

            continue

        if not candidate.exists():
            continue

        last_size = candidate.stat().st_size

        logger.info(
            "Natija: %.2f MiB",
            last_size / 1024 / 1024,
        )

        if last_size <= TARGET_SIZE:
            output_file.unlink(
                missing_ok=True
            )

            candidate.rename(
                output_file
            )

            new_width, new_height = (
                get_video_dimensions(
                    output_file
                )
            )

            # ASL O'LCHAMNI QAT'IY TEKSHIRAMIZ
            if (
                new_width != width
                or new_height != height
            ):
                output_file.unlink(
                    missing_ok=True
                )

                raise RuntimeError(
                    "Video o'lchami o'zgargan. "
                    "Qayta ishlash bekor qilindi."
                )

            return output_file

        candidate.unlink(
            missing_ok=True
        )

    if last_size:
        raise RuntimeError(
            "Videoni 14 MiB gacha siqib bo'lmadi.\n"
            f"Oxirgi hajm: "
            f"{last_size / 1024 / 1024:.2f} MiB"
        )

    raise RuntimeError(
        "Video qayta kodlanmadi."
    )


# =========================================================
# VIDEODAN MP3
# =========================================================

def extract_mp3_from_video_sync(
    video_file,
    output_mp3,
):
    cmd = [
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
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "MP3 yaratishda FFmpeg xatosi:\n"
            + result.stderr[-2000:]
        )

    if not output_mp3.exists():
        raise RuntimeError(
            "MP3 yaratilmadi."
        )

    return output_mp3


# =========================================================
# VIDEO JOB
# =========================================================

def cleanup_old_jobs():
    now = time.time()

    expired = []

    for job_id, job in list(
        VIDEO_JOBS.items()
    ):
        created = job.get(
            "created",
            now,
        )

        if now - created > 3600:
            expired.append(
                job_id
            )

    for job_id in expired:
        job = VIDEO_JOBS.pop(
            job_id,
            None,
        )

        if job:
            workdir = job.get(
                "workdir"
            )

            if workdir:
                shutil.rmtree(
                    workdir,
                    ignore_errors=True,
                )


def create_video_job(
    user_id,
    workdir,
    mp3_path,
):
    cleanup_old_jobs()

    job_id = uuid.uuid4().hex[:16]

    VIDEO_JOBS[job_id] = {
        "user_id": user_id,
        "workdir": str(workdir),
        "mp3_path": (
            str(mp3_path)
            if mp3_path
            else None
        ),
        "created": time.time(),
    }

    return job_id


# =========================================================
# VIDEO YUBORISH
# =========================================================

async def download_and_send_video(
    message,
    url,
    user_id,
):
    workdir = Path(
        tempfile.mkdtemp(
            prefix=f"{user_id}_",
            dir=DOWNLOAD_DIR,
        )
    )

    status = None

    try:
        status = await message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        if is_ok(url):
            source = await asyncio.to_thread(
                download_ok_video_sync,
                url,
                workdir,
            )
        else:
            source = await asyncio.to_thread(
                download_video_sync,
                url,
                workdir,
            )

        final_video = (
            workdir / "final.mp4"
        )

        # HAR DOIM qayta kodlanadi.
        await asyncio.to_thread(
            compress_video_sync,
            source,
            final_video,
        )

        # Video ichida audio borligini tekshiramiz.
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
                    "MP3 yaratilmadi"
                )
                mp3_path = None

        # MP3 tugmasi faqat audio bo'lsa.
        keyboard = None

        if mp3_path and mp3_path.exists():
            job_id = create_video_job(
                user_id,
                workdir,
                mp3_path,
            )

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎵 MP3 yuklash",
                        callback_data=(
                            f"media:{job_id}"
                        ),
                    )
                ]
            ])

        with open(
            final_video,
            "rb",
        ) as video_file:

            await message.reply_video(
                video=InputFile(
                    video_file,
                    filename="video.mp4",
                ),

                caption=CAPTION,

                supports_streaming=True,

                reply_markup=keyboard,
            )

    except Exception as e:
        logger.exception(
            "Video xatosi"
        )

        await message.reply_text(
            "❌ Yuklashda xatolik.\n\n"
            f"{str(e)[:1500]}"
        )

        shutil.rmtree(
            workdir,
            ignore_errors=True,
        )

    finally:
        if status:
            try:
                await status.delete()
            except Exception:
                pass


# =========================================================
# YOUTUBE SEARCH
# =========================================================

def youtube_search_sync(query):
    options = base_ydl_options()

    options.update({
        "extract_flat": True,
        "skip_download": True,
    })

    with yt_dlp.YoutubeDL(options) as ydl:
        data = ydl.extract_info(
            f"ytsearch50:{query}",
            download=False,
        )

    results = []

    if not data:
        return results

    for item in data.get(
        "entries",
        []
    ):
        if not item:
            continue

        video_id = item.get(
            "id"
        )

        if not video_id:
            continue

        results.append({
            "title": item.get(
                "title",
                "Noma'lum",
            ),
            "url": (
                "https://www.youtube.com/watch?v="
                + video_id
            ),
        })

    return results[:50]


# =========================================================
# GOOGLE SEARCH
# =========================================================

def google_search_sync(query):
    if not GOOGLE_API_KEY or not GOOGLE_CX:
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
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CX,
            "q": query,
            "start": start,
            "num": 10,
        }

        try:
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
                [],
            ):
                if item.get("link"):
                    results.append({
                        "title": item.get(
                            "title",
                            "Natija",
                        ),
                        "url": item["link"],
                        "google": True,
                    })

        except Exception:
            logger.exception(
                "Google search xatosi"
            )

    return results[:50]


# =========================================================
# SEARCH KEYBOARD
# =========================================================

def build_search_keyboard(
    user_id,
    page,
):
    data = USER_SEARCH_DATA.get(
        user_id
    )

    if not data:
        return None

    results = data["results"]

    start = page * PAGE_SIZE

    current = results[
        start:start + PAGE_SIZE
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
                callback_data=f"song:{i}",
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
                    callback_data=(
                        f"page:{page - 1}"
                    ),
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
                    callback_data=(
                        f"page:{page + 1}"
                    ),
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
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "👋 Assalomu alaykum!\n\n"
        "Media yuklash uchun havolani yuboring.\n\n"
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
        "qo'shiqchi yoki qo'shiq nomini yozing.\n\n"
        "🎤 Audio yoki voice yuborsangiz, "
        "Shazam orqali aniqlayman."
    )


# =========================================================
# TEXT HANDLER
# =========================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = (
        update.message.text or ""
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
        url = match.group(
            0
        ).rstrip(
            ").,]"
        )

        if not is_supported_url(
            url
        ):
            await update.message.reply_text(
                "❌ Bu havola qo'llab-quvvatlanmaydi."
            )
            return

        # YouTube uchun tanlov.
        if is_youtube(url):
            USER_YOUTUBE_URL[
                update.effective_user.id
            ] = url

            await update.message.reply_text(
                "Kerakli formatni tanlang:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🎬 VIDEO",
                            callback_data="yt:video",
                        ),
                        InlineKeyboardButton(
                            "🎵 MP3",
                            callback_data="yt:mp3",
                        ),
                    ]
                ]),
            )

            return

        # Boshqa barcha saytlar avtomatik video.
        await download_and_send_video(
            update.message,
            url,
            update.effective_user.id,
        )

        return

    # -----------------------------------------------------
    # QO'SHIQ QIDIRISH
    # -----------------------------------------------------

    status = await update.message.reply_text(
        "🔎 Qidirilmoqda..."
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

        # MP3 uchun YouTube asosiy.
        results = yt_results[:50]

        if not results:
            results = google_results[:50]

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
            "results": results,
            "page": 0,
        }

        await status.edit_text(
            "🎵 Natijalar:\n"
            "Kerakli qo'shiqni tanlang:",
            reply_markup=(
                build_search_keyboard(
                    user_id,
                    0,
                )
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
# AUDIO / VOICE
# =========================================================

async def handle_audio(
    update,
    context,
):
    message = update.message

    status = await message.reply_text(
        "🎧 Qo'shiq aniqlanmoqda..."
    )

    workdir = Path(
        tempfile.mkdtemp(
            prefix="shazam_",
            dir=DOWNLOAD_DIR,
        )
    )

    try:
        if message.voice:
            tg_file = await context.bot.get_file(
                message.voice.file_id
            )

            input_file = (
                workdir / "voice.ogg"
            )

            await tg_file.download_to_drive(
                custom_path=str(
                    input_file
                )
            )

        elif message.audio:
            tg_file = await context.bot.get_file(
                message.audio.file_id
            )

            ext = ".mp3"

            if message.audio.file_name:
                ext = (
                    Path(
                        message.audio.file_name
                    ).suffix
                    or ".mp3"
                )

            input_file = (
                workdir
                / f"audio{ext}"
            )

            await tg_file.download_to_drive(
                custom_path=str(
                    input_file
                )
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
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎵 MP3",
                        callback_data=(
                            "shazam:download"
                        ),
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
    query = update.callback_query

    await query.answer()

    data = query.data or ""

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
        url = USER_YOUTUBE_URL.get(
            user_id
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
        url = USER_YOUTUBE_URL.get(
            user_id
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

        status = await query.message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        try:
            p = await asyncio.to_thread(
                download_audio_sync,
                url,
                workdir,
            )

            with open(
                p,
                "rb",
            ) as audio_file:

                await query.message.reply_audio(
                    audio=InputFile(
                        audio_file,
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
                f"{str(e)[:1000]}"
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
                "❌ MP3 fayli muddati tugagan. "
                "Videoni qayta yuboring."
            )
            return

        # Boshqa foydalanuvchi tugmani bosmasin.
        if job.get(
            "user_id"
        ) != user_id:
            await query.message.reply_text(
                "❌ Bu fayl sizga tegishli emas."
            )
            return

        mp3_path = job.get(
            "mp3_path"
        )

        if not mp3_path:
            await query.message.reply_text(
                "❌ Videoda audio topilmadi."
            )
            return

        mp3_file = Path(
            mp3_path
        )

        if not mp3_file.exists():
            await query.message.reply_text(
                "❌ MP3 fayli endi mavjud emas. "
                "Videoni qayta yuboring."
            )
            return

        status = await query.message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        try:
            with open(
                mp3_file,
                "rb",
            ) as audio_file:

                await query.message.reply_audio(
                    audio=InputFile(
                        audio_file,
                        filename="audio.mp3",
                    ),
                    caption=CAPTION,
                )

        except Exception as e:
            logger.exception(
                "Video MP3 yuborish xatosi"
            )

            await query.message.reply_text(
                "❌ MP3 yuborishda xatolik."
            )

        finally:
            try:
                await status.delete()
            except Exception:
                pass

            # MP3 bir marta yuborilgach o'chiramiz.
            VIDEO_JOBS.pop(
                job_id,
                None,
            )

            shutil.rmtree(
                job.get("workdir", ""),
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

        page = max(
            0,
            min(
                page,
                MAX_PAGES - 1,
            ),
        )

        saved = USER_SEARCH_DATA.get(
            user_id
        )

        if not saved:
            return

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
    # SONG RESULT
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

        saved = USER_SEARCH_DATA.get(
            user_id
        )

        if not saved:
            return

        results = saved.get(
            "results",
            [],
        )

        if (
            index < 0
            or index >= len(results)
        ):
            return

        item = results[
            index
        ]

        url = item.get(
            "url"
        )

        if not url:
            return

        workdir = Path(
            tempfile.mkdtemp(
                prefix="song_",
                dir=DOWNLOAD_DIR,
            )
        )

        status = await query.message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        try:
            p = await asyncio.to_thread(
                download_audio_sync,
                url,
                workdir,
            )

            with open(
                p,
                "rb",
            ) as audio_file:

                await query.message.reply_audio(
                    audio=InputFile(
                        audio_file,
                        filename="audio.mp3",
                    ),
                    caption=CAPTION,
                )

        except Exception as e:
            logger.exception(
                "Song MP3 xatosi"
            )

            await query.message.reply_text(
                "❌ MP3 yuklashda xatolik.\n"
                f"{str(e)[:1000]}"
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
    # SHAZAM MP3
    # -----------------------------------------------------

    if data == "shazam:download":
        query_text = context.user_data.get(
            "shazam_query"
        )

        if not query_text:
            await query.message.reply_text(
                "❌ Qo'shiq ma'lumoti topilmadi."
            )
            return

        status = await query.message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        workdir = Path(
            tempfile.mkdtemp(
                prefix="shazam_mp3_",
                dir=DOWNLOAD_DIR,
            )
        )

        try:
            results = await asyncio.to_thread(
                youtube_search_sync,
                query_text,
            )

            if not results:
                await status.edit_text(
                    "❌ Qo'shiq topilmadi."
                )
                return

            p = await asyncio.to_thread(
                download_audio_sync,
                results[0]["url"],
                workdir,
            )

            with open(
                p,
                "rb",
            ) as audio_file:

                await query.message.reply_audio(
                    audio=InputFile(
                        audio_file,
                        filename="audio.mp3",
                    ),
                    caption=CAPTION,
                )

            await status.delete()

        except Exception as e:
            logger.exception(
                "Shazam MP3 xatosi"
            )

            await status.edit_text(
                "❌ MP3 yuklashda xatolik.\n"
                f"{str(e)[:1000]}"
            )

        finally:
            shutil.rmtree(
                workdir,
                ignore_errors=True,
            )

        return


# =========================================================
# ERROR
# =========================================================

async def error_handler(
    update,
    context,
):
    logger.error(
        "Telegram xatosi: %s",
        context.error,
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN Railway Variables ichida yo'q."
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
            start,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO,
            handle_audio,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_text,
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
        "YUKLATGBOT ISHLAYAPTI"
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
