import os
import re
import json
import base64
import asyncio
import logging
import tempfile
import shutil
from pathlib import Path
from urllib.parse import quote_plus

import requests
import yt_dlp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.constants import ChatAction
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

TARGET_SIZE = 14 * 1024 * 1024       # 14 MiB
MAX_SOURCE_SIZE = 1024 * 1024 * 1024 # 1 GB
VIDEO_HEIGHT = 480

DOWNLOAD_DIR = Path("/tmp/bot_downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Google Custom Search ixtiyoriy.
# Railway Variables ga qo'yish mumkin:
# GOOGLE_API_KEY
# GOOGLE_CX
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


# =========================================================
# YOUTUBE COOKIES
# =========================================================

YOUTUBE_COOKIE_FILE = DOWNLOAD_DIR / "youtube_cookies.txt"


def prepare_youtube_cookies():
    """
    Railway Variables:
      YOUTUBE_COOKIES_B64 = base64 encoded cookies.txt

    yoki:
      YOUTUBE_COOKIES = cookies.txt matni
    """

    b64 = os.getenv("YOUTUBE_COOKIES_B64")
    raw = os.getenv("YOUTUBE_COOKIES")

    try:
        if b64:
            data = base64.b64decode(b64).decode("utf-8")
            YOUTUBE_COOKIE_FILE.write_text(data, encoding="utf-8")
            logger.info("YouTube cookies yuklandi.")
            return str(YOUTUBE_COOKIE_FILE)

        if raw:
            YOUTUBE_COOKIE_FILE.write_text(raw, encoding="utf-8")
            logger.info("YouTube cookies yuklandi.")
            return str(YOUTUBE_COOKIE_FILE)

    except Exception:
        logger.exception("YouTube cookies yozishda xato")

    return None


COOKIE_FILE = prepare_youtube_cookies()


# =========================================================
# URL ANIQLASH
# =========================================================

URL_RE = re.compile(r"https?://\S+")


def is_youtube(url: str) -> bool:
    return any(
        x in url.lower()
        for x in (
            "youtube.com",
            "youtu.be",
            "youtube-nocookie.com",
        )
    )


def is_supported_url(url: str) -> bool:
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
    return any(x in url.lower() for x in domains)


# =========================================================
# YT-DLP
# =========================================================

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

    if url and is_youtube(url) and COOKIE_FILE:
        options["cookiefile"] = COOKIE_FILE

    return options


# =========================================================
# VIDEO DOWNLOAD
# =========================================================

def download_video_sync(url: str, output_dir: Path):
    output_template = str(output_dir / "%(title).80s.%(ext)s")

    opts = base_ydl_options(url)

    opts.update({
        "format": (
            "bestvideo[height<=480]+bestaudio/"
            "best[height<=480]/"
            "bestvideo+bestaudio/best"
        ),

        "merge_output_format": "mp4",

        "outtmpl": output_template,

        "postprocessors": [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }
        ],
    })

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

        files = list(output_dir.glob("*"))

        video_files = [
            f for f in files
            if f.is_file()
            and f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")
        ]

        if not video_files:
            raise RuntimeError("Video fayl topilmadi.")

        video_files.sort(
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        return video_files[0], info


# =========================================================
# AUDIO DOWNLOAD
# =========================================================

def download_audio_sync(url: str, output_dir: Path):
    output_template = str(output_dir / "%(title).80s.%(ext)s")

    opts = base_ydl_options(url)

    opts.update({
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

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

        mp3_files = list(output_dir.glob("*.mp3"))

        if not mp3_files:
            raise RuntimeError("MP3 fayl topilmadi.")

        mp3_files.sort(
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        return mp3_files[0], info


# =========================================================
# VIDEO 480P + 14 MB
# =========================================================

def compress_video_sync(input_file: Path, output_file: Path):

    duration = 0

    probe_cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(input_file),
    ]

    import subprocess

    try:
        result = subprocess.run(
            probe_cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        duration = float(result.stdout.strip() or 0)

    except Exception:
        duration = 0

    if duration <= 0:
        duration = 60

    # 14 MiB chegarada ichida qolish uchun xavfsiz bitrate.
    target_kbps = int((TARGET_SIZE * 8 / duration / 1000) * 0.88)

    # Juda katta yoki juda kichik bitrate bo'lmasin
    target_kbps = max(160, min(target_kbps, 1800))

    audio_kbps = 96

    video_kbps = max(
        80,
        target_kbps - audio_kbps
    )

    cmd = [
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

    subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
    )

    # Agar baribir 14 MB dan oshsa, yana siqamiz.
    if output_file.stat().st_size > TARGET_SIZE:

        smaller = output_file.with_name(
            output_file.stem + "_small.mp4"
        )

        smaller_kbps = max(
            80,
            int(video_kbps * 0.70)
        )

        cmd2 = [
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
            f"{smaller_kbps}k",

            "-c:a",
            "aac",

            "-b:a",
            "64k",

            "-movflags",
            "+faststart",

            str(smaller),
        ]

        subprocess.run(
            cmd2,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )

        if smaller.exists():
            output_file.unlink(missing_ok=True)
            smaller.rename(output_file)

    return output_file


# =========================================================
# SEARCH: YOUTUBE 50 TA
# =========================================================

def youtube_search_sync(query: str):
    opts = base_ydl_options()

    opts.update({
        "extract_flat": True,
    })

    with yt_dlp.YoutubeDL(opts) as ydl:

        result = ydl.extract_info(
            f"ytsearch50:{query}",
            download=False
        )

        entries = result.get("entries", [])

        output = []

        for item in entries:
            if not item:
                continue

            video_id = item.get("id")

            if not video_id:
                continue

            title = item.get("title") or "Noma'lum"

            output.append({
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
            })

        return output[:50]


# =========================================================
# GOOGLE SEARCH 50 TA
# =========================================================

def google_search_sync(query: str):
    """
    Google Custom Search API.

    Railway Variables:
      GOOGLE_API_KEY
      GOOGLE_CX

    Agar berilmagan bo'lsa, bo'sh ro'yxat qaytaradi.
    """

    if not GOOGLE_API_KEY or not GOOGLE_CX:
        return []

    results = []

    for start in (1, 11, 21, 31, 41):

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

        for item in data.get("items", []):

            results.append({
                "title": item.get("title", "Noma'lum"),
                "url": item.get("link", ""),
            })

            if len(results) >= 50:
                return results

    return results[:50]


# =========================================================
# SHAZAM
# =========================================================

async def recognize_shazam(file_path: str):
    async with Shazam() as shazam:

        result = await shazam.recognize(file_path)

        track = result.get("track", {})

        title = track.get("title")
        artist = track.get("subtitle")

        if not title:
            return None

        return {
            "title": title,
            "artist": artist or "Noma'lum ijrochi",
            "query": f"{artist or ''} {title}".strip(),
        }


# =========================================================
# PAGINATION
# =========================================================

def make_search_keyboard(user_id: int, page: int, total: int):

    start = page * 10
    end = min(start + 10, total)

    keyboard = []

    for i in range(start, end):

        keyboard.append([
            InlineKeyboardButton(
                f"🎵 {i + 1}. "
                + str(
                    USER_SEARCH_DATA[user_id]["results"][i]["title"]
                )[:55],

                callback_data=f"song:{i}"
            )
        ])

    nav = []

    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=f"page:{page - 1}"
            )
        )

    nav.append(
        InlineKeyboardButton(
            f"{page + 1}/5",
            callback_data="page:current"
        )
    )

    if page < 4 and end < total:
        nav.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=f"page:{page + 1}"
            )
        )

    keyboard.append(nav)

    return InlineKeyboardMarkup(keyboard)


# Foydalanuvchi bo'yicha qidiruv natijalari.
USER_SEARCH_DATA = {}


def search_text(page: int, total: int):
    return (
        f"🎵 <b>Qidiruv natijalari</b>\n\n"
        f"Jami: <b>{total}</b> ta\n"
        f"Ko'rsatilmoqda: <b>{page * 10 + 1}"
        f"-{min(page * 10 + 10, total)}</b>\n\n"
        f"Qo'shiqni tanlang:"
    )


# =========================================================
# VIDEO PROCESS
# =========================================================

async def process_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    audio_only=False,
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

        if audio_only:

            await context.bot.send_chat_action(
                chat_id,
                ChatAction.UPLOAD_AUDIO
            )

            mp3_file, info = await asyncio.to_thread(
                download_audio_sync,
                url,
                work_dir
            )

            await status.edit_text(
                "📤 MP3 yuborilmoqda..."
            )

            with open(mp3_file, "rb") as f:

                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=InputFile(
                        f,
                        filename="audio.mp3"
                    ),

                    title=info.get("title"),
                    performer=(
                        info.get("uploader")
                        or info.get("channel")
                    ),

                    caption="🎵 128 kbps MP3"
                )

            await status.delete()
            return

        # VIDEO
        await context.bot.send_chat_action(
            chat_id,
            ChatAction.UPLOAD_VIDEO
        )

        source_file, info = await asyncio.to_thread(
            download_video_sync,
            url,
            work_dir
        )

        final_file = work_dir / "final_480p.mp4"

        await status.edit_text(
            "⚙️ 480p / 14 MB ga tayyorlanmoqda..."
        )

        await asyncio.to_thread(
            compress_video_sync,
            source_file,
            final_file
        )

        if final_file.stat().st_size > TARGET_SIZE:

            raise RuntimeError(
                "Video 14 MB limitiga sig'madi."
            )

        await status.edit_text(
            "📤 Video yuborilmoqda..."
        )

        with open(final_file, "rb") as f:

            await context.bot.send_video(
                chat_id=chat_id,
                video=InputFile(
                    f,
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

        logger.exception("VIDEO/AUDIO ERROR")

        try
