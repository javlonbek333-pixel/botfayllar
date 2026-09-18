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
import json
import random
import urllib.parse
from pathlib import Path

import requests
import yt_dlp
from shazamio import Shazam

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    FSInputFile,
    InputMediaPhoto,
    InputMediaVideo,
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
# 1. SOZLAMALAR VA KONFIGURATSIYA
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Telegram Bot API limiti 50MB, xavfsiz chegara 45MB qilib belgilangan
TARGET_SIZE = 45 * 1024 * 1024
MAX_SOURCE_SIZE = 1024 * 1024 * 1024  # 1 GB maksimal manba fayli

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
POT_PROVIDER_URL = "http://127.0.0.1:4416"

# User-Agent ro'yxati (Bloklanishlarni oldini olish uchun)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
]

# =========================================================
# 2. LOGGING VA DIAGNOSTIKA
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("YuklatgBot")

# =========================================================
# 3. GLOBAL MA'LUMOTLAR OMBORI (IN-MEMORY CACHE)
# =========================================================

VIDEO_JOBS = {}
USER_SEARCH_DATA = {}
USER_YOUTUBE_URL = {}
USER_STATE = {}

# =========================================================
# 4. YARDAMCHI UTILITY FUNKSIYALARI
# =========================================================

def get_random_user_agent():
    return random.choice(USER_AGENTS)

def cleanup_file_or_dir(path):
    """Fayl yoki papkani xavfsiz o'chirish."""
    try:
        if isinstance(path, str):
            path = Path(path)
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except Exception as e:
        logger.error(f"Tozalashda xatolik ({path}): {e}")

async def cleanup_old_jobs_loop():
    """Eskirgan vaqtinchalik papkalar va joblarni fonda avtomatik tozalaydi."""
    while True:
        try:
            now = time.time()
            expired_jobs = []
            
            for job_id, job in list(VIDEO_JOBS.items()):
                # 30 daqiqadan eski bo'lgan topshiriqlarni o'chirish
                if now - job.get("created", 0) > 1800:
                    expired_jobs.append(job_id)

            for job_id in expired_jobs:
                job = VIDEO_JOBS.pop(job_id, None)
                if job and "workdir" in job:
                    cleanup_file_or_dir(job["workdir"])
                    logger.info(f"Eskirgan job va papka tozalandi: {job_id}")

            # Temp papkadagi qolib ketgan eski fayllarni tozalash (2 soatdan eski)
            for item in DOWNLOAD_DIR.iterdir():
                if item.is_dir() and (now - item.stat().st_mtime > 7200):
                    cleanup_file_or_dir(item)

        except Exception as e:
            logger.error(f"Sistemaviy tozalash siklida xatolik: {e}")

        await asyncio.sleep(600)

def prepare_youtube_cookies():
    """YouTube cookie faylini tayyorlash."""
    try:
        if YOUTUBE_COOKIES_B64:
            path = DOWNLOAD_DIR / "youtube_cookies.txt"
            path.write_bytes(base64.b64decode(YOUTUBE_COOKIES_B64))
            return str(path)

        if YOUTUBE_COOKIES:
            path = DOWNLOAD_DIR / "youtube_cookies.txt"
            path.write_text(YOUTUBE_COOKIES, encoding="utf-8")
            return str(path)

    except Exception as e:
        logger.error(f"YouTube cookies tayyorlashda xatolar: {e}")

    return None

# =========================================================
# 5. URL FILTRLARI VA SHTAMPLAR
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

def is_pinterest(url):
    return "pinterest." in url.lower()

def is_snapchat(url):
    return "snapchat." in url.lower()

def is_likee(url):
    return "likee." in url.lower()

def is_threads(url):
    return "threads." in url.lower()

def is_supported_url(url):
    return any([
        is_youtube(url),
        is_instagram(url),
        is_tiktok(url),
        is_facebook(url),
        is_ok(url),
        is_pinterest(url),
        is_snapchat(url),
        is_likee(url),
        is_threads(url),
    ])

# =========================================================
# 6. FFMPEG VA MEDIA METADATA OPERATSIYALARI (TUZA TILDIGU)
# =========================================================

def get_video_dimensions(file_path):
    """Video o'lchamlarini ffprobe orqali olish."""
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0", str(file_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    try:
        dimensions = result.stdout.strip().split("x")
        if len(dimensions) == 2:
            return int(dimensions[0]), int(dimensions[1])
    except Exception:
        pass
    
    # ffprobe o'qiy olmasa xatolik berilmaydi, standart 1280x720 qaytariladi
    return 1280, 720

def get_duration(file_path):
    """Video yoki audio davomiyligini soniyalarda olish."""
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(file_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except Exception:
        return 0.0

def has_audio_stream(file_path):
    """Video fayl ichida audio potok borligini tekshirish."""
    command = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(file_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    return bool(result.stdout.strip())

def compress_video_sync(input_file, output_file):
    """Videoni siqish va Telegram talablariga moslash."""
    # Fayl hajmi allaqachon limitdan kichik bo'lsa, uni siqish shart emas
    if input_file.stat().st_size <= TARGET_SIZE:
        shutil.copyfile(input_file, output_file)
        return output_file

    duration = get_duration(input_file)
    if duration <= 0:
        duration = 60.0

    audio_exists = has_audio_stream(input_file)
    
    # Bitrate hisoblash
    target_bits = (TARGET_SIZE - (1 * 1024 * 1024)) * 8
    total_bitrate = int(target_bits / duration)
    total_bitrate = max(150_000, min(total_bitrate, 2_000_000))

    if audio_exists:
        audio_bitrate = 96_000
        video_bitrate = max(100_000, total_bitrate - audio_bitrate)
    else:
        audio_bitrate = 0
        video_bitrate = max(100_000, total_bitrate)

    command = [
        "ffmpeg", "-y", "-i", str(input_file),
        "-map", "0:v:0", "-c:v", "libx264",
        "-preset", "veryfast",
        "-b:v", f"{video_bitrate // 1000}k",
        "-vf", "scale='min(1280,iw)':-2",  # Razmerni avtomatik juft pikselga moslash
        "-pix_fmt", "yuv420p"  # Telegram mosligi uchun har doim yuv420p
    ]

    if audio_exists:
        command += ["-map", "0:a:0", "-c:a", "aac", "-b:a", f"{audio_bitrate // 1000}k"]
    else:
        command += ["-an"]

    command += ["-movflags", "+faststart", str(output_file)]

    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0 or not output_file.exists():
        raise RuntimeError("Video qayta ishlashda FFmpeg xatosi yuz berdi.")

    return output_file

def extract_mp3_from_video_sync(video_file, output_mp3):
    """Videodan MP3 ajratib olish."""
    command = [
        "ffmpeg", "-y", "-i", str(video_file),
        "-vn", "-c:a", "libmp3lame", "-b:a", "128k",
        "-ar", "44100", "-ac", "2", str(output_mp3),
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0 or not output_mp3.exists():
        raise RuntimeError("Videodan MP3 ajratishda FFmpeg xatosi yuz berdi.")
    return output_mp3

# =========================================================
# 7. YT-DLP CORE VA DOWNLOADER FUNKSIYALARI
# =========================================================

def base_ydl_options(url=None):
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 8,
        "nocheckcertificate": True,
        "ffmpeg_location": FFMPEG_LOCATION,
        "geo_bypass": True,
        "http_headers": {
            "User-Agent": get_random_user_agent(),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    if url and is_youtube(url):
        cookie_file = prepare_youtube_cookies()
        if cookie_file:
            options["cookiefile"] = cookie_file

        options["extractor_args"] = {
            "youtubepot-bgutilhttp": {
                "base_url": POT_PROVIDER_URL,
            }
        }
        options["remote_components"] = "ejs:github"

    return options

def find_downloaded_file(workdir):
    """Yuklab olingan eng asosiy media faylni qidirib topadi."""
    files = []
    for path in workdir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() in [".part", ".ytdl", ".temp", ".txt", ".json"]:
            continue
        files.append(path)

    if not files:
        return None

    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return files[0]

def download_video_sync(url, workdir):
    """Barcha tarmoqlardan umumiy video yuklab olish funksiyasi."""
    output = str(workdir / "%(title).150s.%(ext)s")
    options = base_ydl_options(url)
    options.update({
        "outtmpl": output,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "max_filesize": MAX_SOURCE_SIZE,
    })

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    path = find_downloaded_file(workdir)

    if not path:
        raise RuntimeError("Video fayli topilmadi.")

    if path.stat().st_size > MAX_SOURCE_SIZE:
        raise RuntimeError("Manba video o'lchami o'ta katta (1 GB dan oshgan).")

    return path

def download_ok_video_sync(url, workdir):
    """OK.ru uchun alohida yuklash posti."""
    output = str(workdir / "%(title).150s.%(ext)s")
    options = base_ydl_options(url)
    options.update({
        "outtmpl": output,
        "format": "best[ext=mp4]/best",
        "merge_output_format": "mp4",
    })

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    path = find_downloaded_file(workdir)

    if not path:
        raise RuntimeError("OK.ru xizmatidan video yuklab bo'lmadi.")

    return path

def download_audio_sync(url, workdir):
    """Manbadan faqat audioni MP3 qilib yuklab olish."""
    output = str(workdir / "%(title).150s.%(ext)s")
    options = base_ydl_options(url)
    options.update({
        "outtmpl": output,
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ],
    })

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    mp3_files = list(workdir.glob("*.mp3"))

    if not mp3_files:
        path = find_downloaded_file(workdir)
        if path:
            mp3_path = workdir / "converted.mp3"
            return extract_mp3_from_video_sync(path, mp3_path)
        raise RuntimeError("MP3 fayli tayyorlanmadi.")

    mp3_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return mp3_files[0]

# =========================================================
# 8. QIDIRUV VA TASHQI API XIZMATLARI
# =========================================================

def youtube_search_sync(query):
    """YouTube bo'yicha qidirish."""
    options = base_ydl_options()
    options.update({"extract_flat": True, "skip_download": True})

    with yt_dlp.YoutubeDL(options) as ydl:
        data = ydl.extract_info(f"ytsearch50:{query}", download=False)

    results = []
    if not data:
        return results

    for item in data.get("entries", []):
        if not item or not item.get("id"):
            continue
        results.append({
            "title": item.get("title", "Noma'lum trek"),
            "url": f"https://www.youtube.com/watch?v={item['id']}",
        })

    return results[:50]

def google_search_sync(query):
    """Google Custom Search API orqali qidirish."""
    if not GOOGLE_API_KEY or not GOOGLE_CX:
        return []

    results = []
    for start in [1, 11, 21, 31, 41]:
        params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_CX, "q": query, "start": start, "num": 10}
        try:
            response = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=15)
            if response.status_code != 200:
                continue
            data = response.json()
            for item in data.get("items", []):
                if item.get("link"):
                    results.append({"title": item.get("title", "Natija"), "url": item["link"], "google": True})
        except Exception as e:
            logger.error(f"Google Search API xatosi: {e}")

    return results[:50]

# =========================================================
# 9. JOB CREATOR VA STATE MANAGEMENT
# =========================================================

def create_video_job(user_id, workdir, mp3_path):
    job_id = uuid.uuid4().hex[:16]
    VIDEO_JOBS[job_id] = {
        "user_id": user_id,
        "workdir": str(workdir),
        "mp3_path": str(mp3_path),
        "created": time.time(),
    }
    return job_id

def build_search_keyboard(user_id, page):
    data = USER_SEARCH_DATA.get(user_id)
    if not data:
        return None

    results = data["results"]
    start = page * PAGE_SIZE
    current = results[start: start + PAGE_SIZE]

    buttons = []
    for i, item in enumerate(current, start=start):
        title = item.get("title", "Noma'lum")[:50]
        buttons.append([InlineKeyboardButton(f"🎵 {i + 1}. {title}", callback_data=f"song:{i}")])

    max_page = min(MAX_PAGES, max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE))

    if max_page > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️", callback_data=f"page:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{max_page}", callback_data="noop"))
        if page < max_page - 1:
            nav.append(InlineKeyboardButton("➡️", callback_data=f"page:{page + 1}"))
        buttons.append(nav)

    return InlineKeyboardMarkup(buttons)

# =========================================================
# 10. BOTNING ASOSIY MANTIQIY ISHLOVCHILARI (HANDLERS)
# =========================================================

async def download_and_send_video(message, url, user_id):
    workdir = Path(tempfile.mkdtemp(prefix=f"{user_id}_", dir=DOWNLOAD_DIR))
    status = None

    try:
        status = await message.reply_text("⏳ Video yuklab olinmoqda va qayta ishlanmoqda...")

        if is_ok(url):
            source = await asyncio.to_thread(download_ok_video_sync, url, workdir)
        else:
            source = await asyncio.to_thread(download_video_sync, url, workdir)

        final_video = workdir / "final.mp4"
        await asyncio.to_thread(compress_video_sync, source, final_video)

        width, height = get_video_dimensions(final_video)
        duration = int(get_duration(final_video))

        mp3_path = None
        if has_audio_stream(source):
            try:
                mp3_path = workdir / "audio.mp3"
                await asyncio.to_thread(extract_mp3_from_video_sync, source, mp3_path)
            except Exception as e:
                logger.error(f"MP3 yaratishda muammo: {e}")
                mp3_path = None

        keyboard = None
        if mp3_path and mp3_path.exists():
            job_id = create_video_job(user_id, workdir, mp3_path)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎵 MP3 variantini yuklash", callback_data=f"media:{job_id}")]
            ])

        await message.reply_video(
            video=FSInputFile(str(final_video)),
            duration=duration,
            width=width,
            height=height,
            caption=CAPTION,
            supports_streaming=True,
            reply_markup=keyboard,
        )

        if not mp3_path:
            cleanup_file_or_dir(workdir)

    except Exception as e:
        logger.exception("Video yuklash jarayonida xato")
        await message.reply_text(f"❌ Yuklashda xatolik yuz berdi.\n\nTafsilot: {str(e)[:1000]}")
        cleanup_file_or_dir(workdir)

    finally:
        if status:
            try:
                await status.delete()
            except Exception:
                pass

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_first_name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Assalomu alaykum, {user_first_name}!\n\n"
        "Media yuklash uchun quyidagi tarmoqlardan birining havolasini yuboring:\n"
        "• Instagram — Post, Stories, Reels\n"
        "• YouTube — Video, Shorts, Audio\n"
        "• TikTok — Video\n"
        "• Facebook — Reels/Video\n"
        "• OK.ru — Video\n"
        "• Pinterest, Snapchat, Likee, Threads\n\n"
        "🎵 Musiqa qidirish uchun qo'shiq nomi yoki ijrochi nomini yozing.\n"
        "🎤 Ovozli xabar yoki audio yuborsangiz, Shazam orqali nomini topib beraman!"
    )

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    match = re.search(r"https?://\S+", text)

    if match:
        url = match.group(0).rstrip(").,]")
        if not is_supported_url(url):
            await update.message.reply_text("❌ Bu havola qo'llab-quvvatlanmaydi yoki qo'shilmagan.")
            return

        if is_youtube(url):
            USER_YOUTUBE_URL[update.effective_user.id] = url
            await update.message.reply_text(
                "YouTube havolasi qabul qilindi. Formatni tanlang:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🎬 VIDEO", callback_data="yt:video"),
                        InlineKeyboardButton("🎵 MP3 AUDIO", callback_data="yt:mp3"),
                    ]
                ]),
            )
            return

        await download_and_send_video(update.message, url, update.effective_user.id)
        return

    # Text bo'yicha musiqa qidiruv
    status = await update.message.reply_text("🔎 Musiqa bazasidan qidirilmoqda...")

    try:
        yt_task = asyncio.to_thread(youtube_search_sync, text)
        google_task = asyncio.to_thread(google_search_sync, text)

        yt_results, google_results = await asyncio.gather(yt_task, google_task, return_exceptions=True)

        if isinstance(yt_results, Exception): yt_results = []
        if isinstance(google_results, Exception): google_results = []

        results = yt_results[:50] or google_results[:50]

        if not results:
            await status.edit_text("❌ So'rovingiz bo'yicha hech narsa topilmadi.")
            return

        user_id = update.effective_user.id
        USER_SEARCH_DATA[user_id] = {"results": results, "page": 0}

        await status.edit_text(
            "🎵 Topilgan musiqa va videolar:\nKeraklisini tanlang:",
            reply_markup=build_search_keyboard(user_id, 0),
        )

    except Exception as e:
        logger.exception("Qidiruv funksiyasida xato")
        await status.edit_text("❌ Qidirish jarayonida kutilmagan xatolik bo'ldi.")

async def handle_audio_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    status = await message.reply_text("🎧 Audio tahlil qilinmoqda (Shazam)...")
    workdir = Path(tempfile.mkdtemp(prefix="shazam_", dir=DOWNLOAD_DIR))

    try:
        if message.voice:
            tg_file = await context.bot.get_file(message.voice.file_id)
            input_file = workdir / "voice.ogg"
            await tg_file.download_to_drive(custom_path=str(input_file))
        elif message.audio:
            tg_file = await context.bot.get_file(message.audio.file_id)
            ext = Path(message.audio.file_name).suffix if message.audio.file_name else ".mp3"
            input_file = workdir / f"audio{ext}"
            await tg_file.download_to_drive(custom_path=str(input_file))
        else:
            return

        shazam = Shazam()
        result = await shazam.recognize(str(input_file))
        track = result.get("track")

        if not track or not track.get("title"):
            await status.edit_text("❌ Afsuski, bu audio/ovozdagi musiqani aniqlay olmadim.")
            return

        title = track.get("title")
        artist = track.get("subtitle", "")
        search_query = f"{artist} {title}".strip()
        context.user_data["shazam_query"] = search_query

        await status.edit_text(
            f"🎵 Qo'shiq aniqlandi!\n\n👤 Ijrochi: {artist}\n🎶 Nomi: {title}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎵 MP3 holida yuklash", callback_data="shazam:download")]
            ]),
        )

    except Exception as e:
        logger.exception("Shazam jarayonida xatolik")
        await status.edit_text("❌ Audioni tanishda xatolik yuz berdi.")
    finally:
        cleanup_file_or_dir(workdir)

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user_id = query.from_user.id

    if data == "noop":
        return

    if data == "yt:video":
        url = USER_YOUTUBE_URL.get(user_id)
        if not url:
            await query.message.reply_text("❌ Havola vaqti tugagan. Qaytadan yuboring.")
            return
        await download_and_send_video(query.message, url, user_id)
        return

    if data == "yt:mp3":
        url = USER_YOUTUBE_URL.get(user_id)
        if not url:
            await query.message.reply_text("❌ Havola topilmadi.")
            return

        workdir = Path(tempfile.mkdtemp(prefix="mp3_", dir=DOWNLOAD_DIR))
        status = await query.message.reply_text("⏳ MP3 audio yuklanmoqda...")

        try:
            p = await asyncio.to_thread(download_audio_sync, url, workdir)
            await query.message.reply_audio(
                audio=FSInputFile(str(p)),
                filename="audio.mp3",
                caption=CAPTION,
            )
        except Exception as e:
            logger.exception("YouTube MP3 yuklashda xato")
            await query.message.reply_text(f"❌ Audio yuklanmadi:\n{str(e)[:1000]}")
        finally:
            try:
                await status.delete()
            except Exception:
                pass
            cleanup_file_or_dir(workdir)
        return

    if data.startswith("media:"):
        job_id = data.split(":", 1)[1]
        job = VIDEO_JOBS.get(job_id)

        if not job:
            await query.message.reply_text("❌ Audio tayyorlash muddati tugagan.")
            return

        if job.get("user_id") != user_id:
            await query.message.reply_text("❌ Bu buyruq sizga tegishli emas.")
            return

        mp3_path = job.get("mp3_path")
        if not mp3_path or not Path(mp3_path).exists():
            await query.message.reply_text("❌ Fayl diskdan o'chirilgan.")
            return

        status = await query.message.reply_text("⏳ Audio yuborilmoqda...")
        try:
            await query.message.reply_audio(audio=FSInputFile(mp3_path), caption=CAPTION)
        except Exception:
            logger.exception("MP3 yuborishda xato")
            await query.message.reply_text("❌ MP3ni yuborib bo'lmadi.")
        finally:
            try:
                await status.delete()
            except Exception:
                pass
            
            job = VIDEO_JOBS.pop(job_id, None)
            if job and "workdir" in job:
                cleanup_file_or_dir(job["workdir"])
        return

    if data.startswith("page:"):
        try:
            page = int(data.split(":", 1)[1])
        except Exception:
            return

        saved = USER_SEARCH_DATA.get(user_id)
        if not saved:
            return

        page = max(0, min(page, MAX_PAGES - 1))
        saved["page"] = page
        keyboard = build_search_keyboard(user_id, page)

        if keyboard:
            try:
                await query.message.edit_reply_markup(reply_markup=keyboard)
            except Exception:
                pass
        return

    if data.startswith("song:"):
        try:
            index = int(data.split(":", 1)[1])
        except Exception:
            return

        saved = USER_SEARCH_DATA.get(user_id)
        if not saved:
            return

        results = saved.get("results", [])
        if index < 0 or index >= len(results):
            return

        item = results[index]
        url = item.get("url")
        if not url:
            return

        if item.get("google"):
            await query.message.reply_text(f"🌐 Google Manbasi:\n{url}")
            return

        workdir = Path(tempfile.mkdtemp(prefix="song_", dir=DOWNLOAD_DIR))
        status = await query.message.reply_text("⏳ MP3 fayl ajratib olinmoqda...")

        try:
            p = await asyncio.to_thread(download_audio_sync, url, workdir)
            await query.message.reply_audio(audio=FSInputFile(str(p)), caption=CAPTION)
        except Exception as e:
            logger.exception("Song MP3 yuklash xatosi")
            await query.message.reply_text(f"❌ Musiqani yuklab bo'lmadi:\n{str(e)[:1000]}")
        finally:
            try:
                await status.delete()
            except Exception:
                pass
            cleanup_file_or_dir(workdir)
        return

    if data == "shazam:download":
        query_text = context.user_data.get("shazam_query")
        if not query_text:
            await query.message.reply_text("❌ Musiqa ma'lumoti izi yo'qolgan.")
            return

        status = await query.message.reply_text("⏳ Musiqa qidirilib yuklanmoqda...")
        workdir = Path(tempfile.mkdtemp(prefix="shazam_mp3_", dir=DOWNLOAD_DIR))

        try:
            results = await asyncio.to_thread(youtube_search_sync, query_text)
            if not results:
                await status.edit_text("❌ Qidiruv natijasida bu musiqa topilmadi.")
                return

            p = await asyncio.to_thread(download_audio_sync, results[0]["url"], workdir)
            await query.message.reply_audio(audio=FSInputFile(str(p)), caption=CAPTION)
            await status.delete()

        except Exception as e:
            logger.exception("Shazam MP3 yuklashda xato")
            await status.edit_text(f"❌ Musiqani yuklab bo'lmadi.\n{str(e)[:1000]}")
        finally:
            cleanup_file_or_dir(workdir)
        return

# =========================================================
# 11. APPLICATION INITIALIZATION & RUNNER
# =========================================================

async def post_init(application: Application):
    """Bot ishga tushishi bilan fondagi vazifalarni ishga tushiradi."""
    asyncio.create_task(cleanup_old_jobs_loop())
    logger.info("Fondagi avtomatik tozalash taymerlari muvaffaqiyatli yoqildi.")

def main():
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN o'zgaruvchisi topilmadi! Dastur to'xtatildi.")
        raise RuntimeError("BOT_TOKEN env variable is missing.")

    logger.info("Bot infratuzilmasi ishga tushirilmoqda...")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )

    # Buyruqlar va xabarlar ishlovchilarini qo'shish
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    application.add_handler(CallbackQueryHandler(callback_query_handler))

    logger.info("YUKLATGBOT BARCHA XIZMATLARI BILAN ISHGA TUSHDI")
    
    # Botni ishga tushirish (Polling)
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
