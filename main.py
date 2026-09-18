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

        try:
            await status.edit_text(
                "❌ Yuklashda xato:\n\n"
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


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "👋 <b>Media yuklovchi bot</b>\n\n"

        "🔗 Havolani yuboring.\n\n"

        "🎵 <b>Qo'shiq qidirish:</b>\n"
        "Ijrochi yoki qo'shiq nomini yozing.\n\n"

        "🎙️ <b>Shazam:</b>\n"
        "Audio yoki voice yuboring — qo'shiqni aniqlayman.\n\n"

        "📌 YouTube uchun:\n"
        "🎬 Video 480p\n"
        "🎵 MP3 128 kbps"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# =========================================================
# TEXT MESSAGE
# =========================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    # URL bo'lsa
    urls = URL_RE.findall(text)

    if urls:

        url = urls[0]

        if is_supported_url(url):

            if is_youtube(url):

                context.user_data["youtube_url"] = url

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
                    "🎬 YouTube media:\n"
                    "Kerakli formatni tanlang:",
                    reply_markup=keyboard
                )

            else:

                await process_video(
                    update,
                    context,
                    url,
                    audio_only=False
                )

            return

    # Oddiy matn — qo'shiq qidirish
    query = text

    status = await update.message.reply_text(
        "🔎 Qo'shiq qidirilmoqda..."
    )

    try:

        youtube_results = await asyncio.to_thread(
            youtube_search_sync,
            query
        )

        google_results = await asyncio.to_thread(
            google_search_sync,
            query
        )

        # Asosiy 50 ta natija — YouTube.
        # Google natijalari alohida saqlanadi.
        results = youtube_results[:50]

        if not results:

            await status.edit_text(
                "❌ Qo'shiq topilmadi."
            )
            return

        user_id = update.effective_user.id

        USER_SEARCH_DATA[user_id] = {
            "results": results,
            "google": google_results[:50],
            "query": query,
        }

        await status.delete()

        await update.message.reply_text(
            search_text(0, len(results)),
            parse_mode="HTML",
            reply_markup=make_search_keyboard(
                user_id,
                0,
                len(results)
            )
        )

        # Google topilgan bo'lsa, qo'shimcha tugma
        if google_results:

            await update.message.reply_text(
                "🌐 Google'da ham natijalar topildi.",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🌐 Google natijalarini ko'rish",
                            callback_data="google:0"
                        )
                    ]
                ])
            )

    except Exception as e:

        logger.exception("SEARCH ERROR")

        await status.edit_text(
            "❌ Qidirishda xato:\n"
            f"{str(e)[:1000]}"
        )


# =========================================================
# VOICE / AUDIO → SHAZAM
# =========================================================

async def handle_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.message

    status = await message.reply_text(
        "🎙️ Shazam aniqlayapti..."
    )

    work_dir = Path(
        tempfile.mkdtemp(
            prefix="shazam_",
            dir=str(DOWNLOAD_DIR)
        )
    )

    try:

        if message.voice:

            tg_file = await message.voice.get_file()

            input_file = work_dir / "voice.ogg"

            await tg_file.download_to_drive(
                custom_path=str(input_file)
            )

        elif message.audio:

            tg_file = await message.audio.get_file()

            extension = ".mp3"

            if message.audio.file_name:
                extension = Path(
                    message.audio.file_name
                ).suffix or ".mp3"

            input_file = work_dir / (
                "audio" + extension
            )

            await tg_file.download_to_drive(
                custom_path=str(input_file)
            )

        else:
            await status.delete()
            return

        result = await recognize_shazam(
            str(input_file)
        )

        if not result:

            await status.edit_text(
                "❌ Qo'shiq aniqlanmadi."
            )

            return

        user_id = update.effective_user.id

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
            "🎵 <b>Qo'shiq topildi!</b>\n\n"
            f"👤 <b>{result['artist']}</b>\n"
            f"🎵 <b>{result['title']}</b>",
            parse_mode="HTML",
            reply_markup=keyboard
        )

    except Exception as e:

        logger.exception("SHAZAM ERROR")

        await status.edit_text(
            "❌ Shazam xatosi:\n"
            f"{str(e)[:1000]}"
        )

    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )


# =========================================================
# CALLBACK
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    data = query.data

    # -----------------------------------------
    # YouTube VIDEO
    # -----------------------------------------

    if data == "yt:video":

        url = context.user_data.get("youtube_url")

        if not url:
            await query.edit_message_text(
                "❌ YouTube havola topilmadi."
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

    # -----------------------------------------
    # YouTube MP3
    # -----------------------------------------

    if data == "yt:mp3":

        url = context.user_data.get("youtube_url")

        if not url:
            await query.edit_message_text(
                "❌ YouTube havola topilmadi."
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

    # -----------------------------------------
    # PAGE
    # -----------------------------------------

    if data.startswith("page:"):

        page_value = data.split(":")[1]

        if page_value == "current":
            return

        page = int(page_value)

        user_data = USER_SEARCH_DATA.get(user_id)

        if not user_data:
            await query.edit_message_text(
                "❌ Qidiruv natijalari eskirgan."
            )
            return

        results = user_data["results"]

        await query.edit_message_text(
            search_text(page, len(results)),
            parse_mode="HTML",
            reply_markup=make_search_keyboard(
                user_id,
                page,
                len(results)
            )
        )

        return

    # -----------------------------------------
    # SONG
    # -----------------------------------------

    if data.startswith("song:"):

        index = int(data.split(":")[1])

        user_data = USER_SEARCH_DATA.get(user_id)

        if not user_data:
            await query.edit_message_text(
                "❌ Natijalar topilmadi."
            )
            return

        results = user_data["results"]

        if index >= len(results):
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

    # -----------------------------------------
    # SHAZAM MP3
    # -----------------------------------------

    if data == "shazam:download":

        user_data = USER_SEARCH_DATA.get(user_id)

        if not user_data:
            await query.edit_message_text(
                "❌ Shazam natijasi topilmadi."
            )
            return

        shazam_result = user_data.get("shazam")

        if not shazam_result:
            await query.edit_message_text(
                "❌ Shazam natijasi topilmadi."
            )
            return

        search_query = shazam_result["query"]

        try:
            await query.edit_message_text(
                "⏳ Qo'shiq YouTube'dan topilmoqda..."
            )

            results = await asyncio.to_thread(
                youtube_search_sync,
                search_query
            )

            if not results:
                await query.edit_message_text(
                    "❌ Qo'shiqni yuklash uchun "
                    "YouTube'dan topa olmadim."
                )
                return

            url = results[0]["url"]

            await query.message.delete()

            await process_video(
                update,
                context,
                url,
                audio_only=True
            )

        except Exception as e:

            logger.exception("SHAZAM DOWNLOAD ERROR")

            try:
                await query.edit_message_text(
                    f"❌ Xato:\n{str(e)[:1000]}"
                )
            except Exception:
                pass

        return

    # -----------------------------------------
    # GOOGLE
    # -----------------------------------------

    if data.startswith("google:"):

        page = int(data.split(":")[1])

        user_data = USER_SEARCH_DATA.get(user_id)

        if not user_data:
            await query.edit_message_text(
                "❌ Google natijalari topilmadi."
            )
            return

        results = user_data.get("google", [])

        if not results:
            await query.edit_message_text(
                "❌ Google API sozlanmagan yoki "
                "natija topilmadi."
            )
            return

        start = page * 10
        end = min(start + 10, len(results))

        keyboard = []

        for i in range(start, end):

            item = results[i]

            keyboard.append([
                InlineKeyboardButton(
                    f"🌐 {i + 1}. "
                    + item["title"][:55],
                    url=item["url"]
                )
            ])

        nav = []

        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "⬅️",
                    callback_data=f"google:{page - 1}"
                )
            )

        nav.append(
            InlineKeyboardButton(
                f"{page + 1}/5",
                callback_data="google:current"
            )
        )

        if page < 4 and end < len(results):
            nav.append(
                InlineKeyboardButton(
                    "➡️",
                    callback_data=f"google:{page + 1}"
                )
            )

        keyboard.append(nav)

        await query.edit_message_text(
            "🌐 <b>Google natijalari</b>\n\n"
            f"{start + 1}-{end} / {len(results)}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.exception(
        "Unhandled exception:",
        exc_info=context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN Railway Variables'da yo'q."
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
        MessageHandler(
            filters.VOICE | filters.AUDIO,
            handle_audio
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    app.add_error_handler(
        error_handler
    )

    logger.info(
        "BOT ISHLAYAPTI..."
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
