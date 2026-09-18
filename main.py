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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
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

TARGET_SIZE = 14 * 1024 * 1024       # 14 MiB
MAX_SOURCE_SIZE = 1024 * 1024 * 1024 # 1 GB

DOWNLOAD_DIR = Path("/tmp/bot_downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")

FFMPEG_LOCATION = os.getenv("FFMPEG_LOCATION", "/usr/bin")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# YOUTUBE COOKIES
# ============================================================

def prepare_youtube_cookies():
    """
    Railway Variables:
    
    YOUTUBE_COOKIES_B64 = base64 ko'rinishidagi cookies.txt
    
    yoki
    
    YOUTUBE_COOKIES = oddiy cookies.txt matni
    """

    b64 = os.getenv("YOUTUBE_COOKIES_B64")
    plain = os.getenv("YOUTUBE_COOKIES")

    if not b64 and not plain:
        return None

    cookie_file = DOWNLOAD_DIR / "youtube_cookies.txt"

    try:
        if b64:
            data = base64.b64decode(b64).decode("utf-8")
        else:
            data = plain

        cookie_file.write_text(data, encoding="utf-8")

        logger.info("YouTube cookies tayyor: %s", cookie_file)

        return str(cookie_file)

    except Exception as e:
        logger.error("Cookies xatosi: %s", e)
        return None


# ============================================================
# URL TEKSHIRISH
# ============================================================

def is_youtube(url):
    return bool(
        re.search(
            r"(youtube\.com|youtu\.be)",
            url,
            re.IGNORECASE,
        )
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

    return any(
        domain in url.lower()
        for domain in domains
    )


# ============================================================
# YT-DLP ASOSIY SOZLAMALAR
# ============================================================

def base_ydl_options(url=None):

    options = {
        "quiet": True,
        "no_warnings": True,

        "retries": 5,
        "fragment_retries": 5,

        "socket_timeout": 30,

        "concurrent_fragment_downloads": 8,

        "nocheckcertificate": True,

        "ffmpeg_location": FFMPEG_LOCATION,

        "max_filesize": MAX_SOURCE_SIZE,

        "noplaylist": True,

        "geo_bypass": True,

        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"]
            }
        },
    }

    if url and is_youtube(url):

        cookie_file = prepare_youtube_cookies()

        if cookie_file:
            options["cookiefile"] = cookie_file

    return options


# ============================================================
# FAYLNI TOPISH
# ============================================================

def find_downloaded_file(folder: Path, extensions=None):

    files = []

    for p in folder.iterdir():

        if not p.is_file():
            continue

        if extensions:
            if p.suffix.lower() not in extensions:
                continue

        files.append(p)

    if not files:
        return None

    return max(
        files,
        key=lambda x: x.stat().st_size
    )


# ============================================================
# VIDEO YUKLASH
# ============================================================

def download_video_sync(url, folder):

    options = base_ydl_options(url)

    options.update({
        "format":
            "bestvideo[height<=480]+bestaudio/"
            "best[height<=480]/"
            "bestvideo+bestaudio/best",

        "merge_output_format": "mp4",

        "outtmpl": str(
            folder / "%(id)s.%(ext)s"
        ),

        "postprocessors": [],

        "overwrites": True,
    })

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(
            url,
            download=True
        )

        downloaded = ydl.prepare_filename(info)

        downloaded_path = Path(downloaded)

        if downloaded_path.exists():
            return downloaded_path

    return find_downloaded_file(
        folder,
        {".mp4", ".mkv", ".webm", ".mov"}
    )


# ============================================================
# AUDIO YUKLASH
# ============================================================

def download_audio_sync(url, folder):

    options = base_ydl_options(url)

    options.update({
        "format": "bestaudio/best",

        "outtmpl": str(
            folder / "%(id)s.%(ext)s"
        ),

        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ],

        "overwrites": True,
    })

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        original = Path(
            ydl.prepare_filename(info)
        )

        mp3 = original.with_suffix(".mp3")

        if mp3.exists():
            return mp3

    return find_downloaded_file(
        folder,
        {".mp3"}
    )


# ============================================================
# VIDEO DAVOMIYLIGI
# ============================================================

def get_duration(file_path):

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

        return float(result.stdout.strip())

    except Exception:
        return 60.0


# ============================================================
# VIDEO 480P + 14 MB
# ============================================================

def compress_video_sync(input_file, output_file):

    duration = get_duration(input_file)

    if duration <= 0:
        duration = 60

    # 14 MiB dan biroz pastroq target
    target_bits = int(
        (TARGET_SIZE * 8 * 0.92) / duration
    )

    audio_bitrate = 64000

    video_bitrate = target_bits - audio_bitrate

    if video_bitrate < 100000:
        video_bitrate = 100000

    video_kbps = int(video_bitrate / 1000)

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
        "64k",

        "-movflags",
        "+faststart",

        str(output_file),
    ]

    logger.info(
        "FFmpeg boshlanmoqda: %s kbps",
        video_kbps
    )

    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # Agar hali ham 14 MiB dan katta bo'lsa,
    # yana kuchliroq siqamiz.
    if output_file.stat().st_size > TARGET_SIZE:

        logger.info(
            "Video 14 MiB dan katta. Qayta siqilmoqda..."
        )

        smaller_kbps = max(
            80,
            int(video_kbps * 0.70)
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
            f"{smaller_kbps}k",

            "-maxrate",
            f"{smaller_kbps}k",

            "-bufsize",
            f"{smaller_kbps * 2}k",

            "-c:a",
            "aac",

            "-b:a",
            "48k",

            "-movflags",
            "+faststart",

            str(output_file),
        ]

        subprocess.run(
            command2,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    # Oxirgi tekshiruv
    if output_file.stat().st_size > TARGET_SIZE:

        raise RuntimeError(
            "Video 14 MiB limitiga sigmadi."
        )

    return output_file


# ============================================================
# YOUTUBE QIDIRUV
# ============================================================

def youtube_search_sync(query):

    options = base_ydl_options()

    options.update({
        "extract_flat": True,
        "skip_download": True,
        "playlistend": 50,
    })

    search_query = f"ytsearch50:{query}"

    results = []

    try:

        with yt_dlp.YoutubeDL(options) as ydl:

            data = ydl.extract_info(
                search_query,
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

                url = item.get(
                    "webpage_url"
                ) or item.get(
                    "url"
                )

                if not url:
                    video_id = item.get("id")

                    if video_id:
                        url = (
                            "https://www.youtube.com/watch?v="
                            + video_id
                        )

                if url:
                    results.append(
                        {
                            "title": title,
                            "url": url,
                        }
                    )

    except Exception as e:

        logger.error(
            "YouTube search xatosi: %s",
            e
        )

    return results[:50]


# ============================================================
# GOOGLE QIDIRUV
# ============================================================

def google_search_sync(query):

    if not GOOGLE_API_KEY or not GOOGLE_CX:
        return []

    results = []

    try:

        for start in [1, 11, 21, 31, 41]:

            url = (
                "https://www.googleapis.com/customsearch/v1"
            )

            params = {
                "key": GOOGLE_API_KEY,
                "cx": GOOGLE_CX,
                "q": query,
                "start": start,
                "num": 10,
            }

            response = requests.get(
                url,
                params=params,
                timeout=20,
            )

            response.raise_for_status()

            data = response.json()

            for item in data.get(
                "items",
                []
            ):

                results.append(
                    {
                        "title": item.get(
                            "title",
                            "Noma'lum"
                        ),
                        "url": item.get(
                            "link"
                        ),
                    }
                )

    except Exception as e:

        logger.error(
            "Google search xatosi: %s",
            e
        )

    return results[:50]


# ============================================================
# SHAZAM
# ============================================================

async def recognize_shazam(file_path):

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
            artist = "Noma'lum ijrochi"

        return {
            "title": title,
            "artist": artist,
            "query": f"{artist} {title}",
        }

    except Exception as e:

        logger.error(
            "Shazam xatosi: %s",
            e
        )

        return None


# ============================================================
# QIDIRUV MA'LUMOTLARI
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

        title = results[index]["title"]

        if len(title) > 55:
            title = title[:55] + "..."

        buttons.append(
            [
                InlineKeyboardButton(
                    f"🎵 {title}",
                    callback_data=f"song:{index}"
                )
            ]
        )

    navigation = []

    if page > 0:

        navigation.append(
            InlineKeyboardButton(
                "⬅️ Oldingi",
                callback_data=f"page:{page - 1}"
            )
        )

    if page < MAX_PAGES - 1 and end < len(results):

        navigation.append(
            InlineKeyboardButton(
                "Keyingi ➡️",
                callback_data=f"page:{page + 1}"
            )
        )

    if navigation:
        buttons.append(navigation)

    # Google natijalari
    google_results = data.get(
        "google",
        []
    )

    if google_results:

        buttons.append(
            [
                InlineKeyboardButton(
                    "🌐 Google natijalari",
                    callback_data="google:current"
                )
            ]
        )

    return InlineKeyboardMarkup(buttons)


# ============================================================
# VIDEO / MP3 ISHLASH
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

        await message.edit_text(
            "⏳ Yuklanmoqda..."
        )

    except Exception:

        try:
            await message.reply_text(
                "⏳ Yuklanmoqda..."
            )
        except Exception:
            pass

    try:

        if mode == "mp3":

            mp3_file = await asyncio.to_thread(
                download_audio_sync,
                url,
                work_dir
            )

            if not mp3_file or not mp3_file.exists():
                raise RuntimeError(
                    "MP3 fayl topilmadi."
                )

            await message.reply_audio(
                audio=InputFile(
                    mp3_file.open("rb"),
                    filename="audio.mp3"
                ),
                title="MP3 128 kbps",
            )

            return

        # VIDEO
        source_file = await asyncio.to_thread(
            download_video_sync,
            url,
            work_dir
        )

        if not source_file or not source_file.exists():

            raise RuntimeError(
                "Video fayl topilmadi."
            )

        final_file = work_dir / "final_480p.mp4"

        await asyncio.to_thread(
            compress_video_sync,
            source_file,
            final_file
        )

        if final_file.stat().st_size > TARGET_SIZE:

            raise RuntimeError(
                "Yakuniy video 14 MiB dan katta."
            )

        await message.reply_video(
            video=InputFile(
                final_file.open("rb"),
                filename="video_480p.mp4"
            ),
            supports_streaming=True,
        )

    except Exception as e:

        logger.exception(
            "Media processing xatosi"
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
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "👋 Assalomu alaykum!\n\n"
        "📥 Media yuklash uchun havolani yuboring.\n\n"

        "Qo‘llab-quvvatlanadi:\n"
        "▶️ YouTube\n"
        "📸 Instagram\n"
        "🎵 TikTok\n"
        "📘 Facebook\n"
        "🟠 OK.ru\n"
        "📌 Pinterest\n"
        "👻 Snapchat\n"
        "❤️ Likee\n"
        "🧵 Threads\n\n"

        "🎵 Qo‘shiq qidirish:\n"
        "Ijrochi nomi, qo‘shiq nomi yoki "
        "matnidan parcha yuboring.\n\n"

        "🎙️ Qo‘shiqni audio/voice ko‘rinishida "
        "yuborsangiz, Shazam orqali aniqlanadi."
    )

    await update.message.reply_text(
        text
    )


# ============================================================
# MATN QABUL QILISH
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

    # URL
    if is_supported_url(text):

        if is_youtube(text):

            context.user_data[
                "youtube_url"
            ] = text

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🎬 VIDEO 480p",
                            callback_data="yt:video"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🎵 MP3 128 kbps",
                            callback_data="yt:mp3"
                        )
                    ],
                ]
            )

            await update.message.reply_text(
                "YouTube havolasi qabul qilindi.\n"
                "Kerakli formatni tanlang:",
                reply_markup=keyboard
            )

        else:

            await update.message.reply_text(
                "⏳ Yuklanmoqda..."
            )

            # yuborilgan xabarni status sifatida ishlatish
            # process_video yangi status yuborishi shart emas
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
        "🔎 Qo‘shiq qidirilmoqda..."
    )

    try:

        youtube_results, google_results = await asyncio.gather(
            asyncio.to_thread(
                youtube_search_sync,
                text
            ),
            asyncio.to_thread(
                google_search_sync,
                text
            ),
        )

        USER_SEARCH_DATA[
            update.effective_user.id
        ] = {
            "query": text,
            "youtube": youtube_results,
            "google": google_results,
            "page": 0,
        }

        if not youtube_results:

            await status.edit_text(
                "❌ Qo‘shiq topilmadi."
            )

            return

        await status.edit_text(
            f"🎵 {len(youtube_results)} ta natija topildi.\n"
            "10 tadan ko‘rsatilmoqda:",
            reply_markup=make_search_keyboard(
                update.effective_user.id,
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
# AUDIO / VOICE
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

        if message.voice:

            telegram_file = await context.bot.get_file(
                message.voice.file_id
            )

            input_file = (
                work_dir / "voice.ogg"
            )

            await telegram_file.download_to_drive(
                custom_path=str(input_file)
            )

        elif message.audio:

            telegram_file = await context.bot.get_file(
                message.audio.file_id
            )

            ext = Path(
                message.audio.file_name or "audio.mp3"
            ).suffix or ".mp3"

            input_file = (
                work_dir / f"audio{ext}"
            )

            await telegram_file.download_to_drive(
                custom_path=str(input_file)
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

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🎵 MP3 128 kbps",
                        callback_data="shazam:download"
                    )
                ]
            ]
        )

        await status.edit_text(
            "🎵 Qo‘shiq aniqlandi!\n\n"
            f"👤 Ijrochi: {result['artist']}\n"
            f"🎵 Nomi: {result['title']}",
            reply_markup=keyboard
        )

    except Exception as e:

        logger.exception(
            "Audio/Shazam xatosi"
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
                "❌ YouTube havolasi topilmadi."
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
                "❌ YouTube havolasi topilmadi."
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
    # SAHIFA
    # ========================================================

    if data.startswith("page:"):

        try:
            page = int(
                data.split(":")[1]
            )
        except Exception:
            page = 0

        search_data = USER_SEARCH_DATA.get(
            user_id
        )

        if not search_data:

            await query.message.reply_text(
                "❌ Qidiruv ma'lumoti eskirgan."
            )

            return

        search_data["page"] = page

        await query.edit_message_reply_markup(
            reply_markup=make_search_keyboard(
                user_id,
                page
            )
        )

        return

    # ========================================================
    # SONG
    # ========================================================

    if data.startswith("song:"):

        try:
            index = int(
                data.split(":")[1]
            )
        except Exception:

            await query.message.reply_text(
                "❌ Natija xatosi."
            )

            return

        search_data = USER_SEARCH_DATA.get(
            user_id
        )

        if not search_data:

            await query.message.reply_text(
                "❌ Qidiruv ma'lumoti topilmadi."
            )

            return

        results = search_data.get(
            "youtube",
            []
        )

        if index < 0 or index >= len(results):

            await query.message.reply_text(
                "❌ Natija topilmadi."
            )

            return

        selected = results[index]

        await query.message.reply_text(
            "⏳ 128 kbps MP3 tayyorlanmoqda..."
        )

        work_dir = Path(
            tempfile.mkdtemp(
                dir=DOWNLOAD_DIR
            )
        )

        try:

            mp3_file = await asyncio.to_thread(
                download_audio_sync,
                selected["url"],
                work_dir
            )

            if not mp3_file or not mp3_file.exists():

                raise RuntimeError(
                    "MP3 fayl yaratilmadi."
                )

            await query.message.reply_audio(
                audio=InputFile(
                    mp3_file.open("rb"),
                    filename="song.mp3"
                ),
                title=selected["title"][:100],
            )

        except Exception as e:

            logger.exception(
                "Song download xatosi"
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
    # SHAZAM MP3
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

        search_query = result["query"]

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
                search_query
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

            if not mp3_file or not mp3_file.exists():

                raise RuntimeError(
                    "MP3 fayl topilmadi."
                )

            await query.message.reply_audio(
                audio=InputFile(
                    mp3_file.open("rb"),
                    filename="shazam_song.mp3"
                ),
                title=(
                    f"{result['artist']} - "
                    f"{result['title']}"
                )[:100],
            )

        except Exception as e:

            logger.exception(
                "Shazam MP3 xatosi"
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

        search_data = USER_SEARCH_DATA.get(
            user_id
        )

        if not search_data:

            await query.message.reply_text(
                "❌ Google qidiruv ma'lumoti topilmadi."
            )

            return

        google_results = search_data.get(
            "google",
            []
        )

        if not google_results:

            await query.message.reply_text(
                "❌ Google natijalari mavjud emas."
            )

            return

        buttons = []

        for i, item in enumerate(
            google_results[:10]
        ):

            title = item.get(
                "title",
                "Natija"
            )

            if len(title) > 55:
                title = title[:55] + "..."

            url = item.get(
                "url"
            )

            if not url:
                continue

            buttons.append(
                [
                    InlineKeyboardButton(
                        f"🌐 {title}",
                        url=url
                    )
                ]
            )

        await query.message.reply_text(
            "🌐 Google qidiruv natijalari:",
            reply_markup=InlineKeyboardMarkup(
                buttons
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

    logger.exception(
        "Telegram bot xatosi:",
        exc_info=context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN Railway Variables'da yo‘q!"
        )

    logger.info(
        "FFmpeg location: %s",
        FFMPEG_LOCATION
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

        logger.info(
            "FFmpeg ishlayapti: %s",
            result.stdout.splitlines()[0]
            if result.stdout
            else "OK"
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

    # /start
    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # Callback
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # Voice
    application.add_handler(
        MessageHandler(
            filters.VOICE,
            handle_audio
        )
    )

    # Audio
    application.add_handler(
        MessageHandler(
            filters.AUDIO,
            handle_audio
        )
    )

    # Text
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
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


if __name__ == "__main__":
    main()
