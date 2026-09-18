import os
import re
import asyncio
import logging
import tempfile
import shutil
import subprocess
import uuid
from pathlib import Path

import yt_dlp
from shazamio import Shazam

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    FSInputFile,
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

# ============================================================
# SOZLAMALAR
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

MAX_VIDEO_BYTES = 14 * 1024 * 1024
MAX_SOURCE_BYTES = 1024 * 1024 * 1024

WORK_DIR = Path(tempfile.gettempdir()) / "telegram_downloader"
WORK_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# YouTube cookie:
# Railway Variables ichida YOUTUBE_COOKIES_B64 yoki YOUTUBE_COOKIES
COOKIE_FILE = WORK_DIR / "youtube_cookies.txt"

# vaqtinchalik video ishlarini saqlash
VIDEO_JOBS = {}


# ============================================================
# URL ANIQLASH
# ============================================================

URL_RE = re.compile(
    r"https?://[^\s]+",
    re.IGNORECASE
)


def extract_url(text: str):
    if not text:
        return None

    match = URL_RE.search(text)

    if not match:
        return None

    return match.group(0).rstrip(".,!?)]}")


def detect_site(url: str):
    u = url.lower()

    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"

    if "instagram.com" in u:
        return "instagram"

    if "tiktok.com" in u or "vm.tiktok.com" in u:
        return "tiktok"

    if "facebook.com" in u or "fb.watch" in u:
        return "facebook"

    if "ok.ru" in u or "odnoklassniki.ru" in u:
        return "ok"

    if "pinterest." in u or "pin.it" in u:
        return "pinterest"

    if "snapchat.com" in u:
        return "snapchat"

    if "likee.video" in u:
        return "likee"

    if "threads.net" in u:
        return "threads"

    return "other"


# ============================================================
# COOKIE
# ============================================================

def prepare_youtube_cookies():
    """
    YOUTUBE_COOKIES_B64 ishlatilsa base64 decode qilish.
    YOUTUBE_COOKIES oddiy cookies.txt bo'lsa to'g'ridan-to'g'ri yozish.
    """

    import base64

    b64 = os.getenv("YOUTUBE_COOKIES_B64")
    plain = os.getenv("YOUTUBE_COOKIES")

    try:
        if b64:
            data = base64.b64decode(b64).decode("utf-8", errors="ignore")

            COOKIE_FILE.write_text(
                data,
                encoding="utf-8"
            )

            return str(COOKIE_FILE)

        if plain:
            COOKIE_FILE.write_text(
                plain,
                encoding="utf-8"
            )

            return str(COOKIE_FILE)

    except Exception as e:
        logger.error("Cookie xatosi: %s", e)

    return None


# ============================================================
# FFMPEG
# ============================================================

def run_cmd(cmd, timeout=600):
    logger.info("CMD: %s", " ".join(map(str, cmd)))

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        logger.error(result.stderr[-5000:])

    return result


def ffprobe_json(path: Path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(path),
    ]

    result = run_cmd(cmd, timeout=120)

    if result.returncode != 0:
        return None

    import json

    try:
        return json.loads(result.stdout)
    except Exception:
        return None


def is_real_video_file(path: Path):
    data = ffprobe_json(path)

    if not data:
        return False

    streams = data.get("streams", [])

    return any(
        s.get("codec_type") == "video"
        for s in streams
    )


def has_audio_track(path: Path):
    data = ffprobe_json(path)

    if not data:
        return False

    streams = data.get("streams", [])

    return any(
        s.get("codec_type") == "audio"
        for s in streams
    )


def get_video_dimensions(path: Path):
    data = ffprobe_json(path)

    if not data:
        return None, None

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = stream.get("width")
            height = stream.get("height")

            if width and height:
                return int(width), int(height)

    return None, None


def get_duration(path: Path):
    data = ffprobe_json(path)

    if not data:
        return 0

    try:
        return float(
            data.get("format", {}).get("duration", 0)
        )
    except Exception:
        return 0


# ============================================================
# FAYL TOPISH
# ============================================================

def find_video_file(folder: Path):
    candidates = []

    for p in folder.rglob("*"):
        if not p.is_file():
            continue

        if p.suffix.lower() in (
            ".mp4",
            ".mkv",
            ".webm",
            ".mov",
            ".avi",
            ".flv",
            ".m4v",
        ):
            candidates.append(p)

    real = [
        p for p in candidates
        if is_real_video_file(p)
    ]

    if not real:
        return None

    # MP4 birinchi
    real.sort(
        key=lambda p: (
            p.suffix.lower() != ".mp4",
            -p.stat().st_mtime,
        )
    )

    return real[0]


# ============================================================
# YT-DLP
# ============================================================

def base_ydl_options(folder: Path):
    options = {
        "outtmpl": str(folder / "%(id)s.%(ext)s"),

        "noplaylist": True,

        "quiet": True,
        "no_warnings": True,

        "retries": 3,
        "fragment_retries": 3,

        "concurrent_fragment_downloads": 8,

        "socket_timeout": 30,

        "merge_output_format": "mp4",

        "max_filesize": MAX_SOURCE_BYTES,

        "remote_components": "ejs:github",

        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0 Safari/537.36"
            )
        },
    }

    cookie_file = prepare_youtube_cookies()

    if cookie_file:
        options["cookiefile"] = cookie_file

    return options


def download_video_sync(url: str, folder: Path):
    """
    Video source'ni yuklab oladi.
    Yakuniy 14 MB limit bu yerda emas,
    keyingi encode bosqichida bajariladi.
    """

    options = base_ydl_options(folder)

    options["format"] = (
        "bestvideo*+bestaudio/"
        "best"
    )

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(
            url,
            download=True
        )

    path = find_video_file(folder)

    if not path:
        raise RuntimeError(
            "Yuklangan haqiqiy video fayl topilmadi."
        )

    return path, info


def download_audio_sync(url: str, folder: Path):
    options = base_ydl_options(folder)

    options["format"] = "bestaudio/best"

    options["postprocessors"] = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }
    ]

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.extract_info(
            url,
            download=True
        )

    mp3_files = list(folder.glob("*.mp3"))

    if not mp3_files:
        raise RuntimeError(
            "MP3 fayl hosil bo'lmadi."
        )

    mp3_files.sort(
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    return mp3_files[0]


# ============================================================
# VIDEO KODLASH
# ============================================================

def encode_video_to_limit(
    source: Path,
    output: Path,
):
    """
    Muhim:
    - width/height o'zgarmaydi
    - aspect ratio o'zgarmaydi
    - har doim qayta kodlanadi
    - H.264
    - 14 MiB limit
    """

    width, height = get_video_dimensions(source)

    if not width or not height:
        raise RuntimeError(
            "Video o'lchamini aniqlab bo'lmadi."
        )

    duration = get_duration(source)

    if duration <= 0:
        raise RuntimeError(
            "Video davomiyligini aniqlab bo'lmadi."
        )

    source_has_audio = has_audio_track(source)

    # 14 MiB dan ozgina past target
    target_bytes = int(
        MAX_VIDEO_BYTES * 0.97
    )

    # container uchun ozgina joy
    audio_bitrate = 96000 if source_has_audio else 0

    available_bits = (
        target_bytes * 8
        - int(duration * audio_bitrate)
    )

    video_bitrate = int(
        available_bits / duration
    )

    # Juda yuqori yoki past qiymatlarni cheklaymiz
    video_bitrate = max(
        100_000,
        min(video_bitrate, 8_000_000)
    )

    # Bir necha urinish
    bitrates = [
        video_bitrate,
        int(video_bitrate * 0.85),
        int(video_bitrate * 0.70),
        int(video_bitrate * 0.55),
        int(video_bitrate * 0.42),
        int(video_bitrate * 0.32),
        int(video_bitrate * 0.24),
        int(video_bitrate * 0.18),
        int(video_bitrate * 0.13),
    ]

    # takrorlarni olib tashlash
    bitrates = list(dict.fromkeys(
        max(80_000, int(x))
        for x in bitrates
    ))

    for bitrate in bitrates:

        if output.exists():
            output.unlink()

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(source),

            "-map",
            "0:v:0",
        ]

        if source_has_audio:
            cmd += [
                "-map",
                "0:a:0?",
            ]

        cmd += [
            "-c:v",
            "libx264",

            "-b:v",
            str(bitrate),

            "-maxrate",
            str(int(bitrate * 1.15)),

            "-bufsize",
            str(int(bitrate * 2)),

            "-preset",
            "veryfast",

            "-profile:v",
            "high",

            "-pix_fmt",
            (
                "yuv444p"
                if width % 2 or height % 2
                else "yuv420p"
            ),

            "-movflags",
            "+faststart",
        ]

        if source_has_audio:
            cmd += [
                "-c:a",
                "aac",
                "-b:a",
                "96k",
            ]

        cmd += [
            str(output)
        ]

        result = run_cmd(
            cmd,
            timeout=1200
        )

        if result.returncode != 0:
            continue

        if not output.exists():
            continue

        size = output.stat().st_size

        if size <= MAX_VIDEO_BYTES:

            # o'lcham o'zgargan-o'zgarmaganini tekshirish
            new_width, new_height = get_video_dimensions(
                output
            )

            if (
                new_width == width
                and new_height == height
            ):
                logger.info(
                    "Video tayyor: %s x %s, %.2f MiB",
                    new_width,
                    new_height,
                    size / 1024 / 1024,
                )

                return output

    # Oxirgi natija ham limitdan katta bo'lsa
    if output.exists():
        final_size = output.stat().st_size

        raise RuntimeError(
            f"Video 14 MiB ga sig'madi. "
            f"Oxirgi hajm: "
            f"{final_size / 1024 / 1024:.2f} MiB"
        )

    raise RuntimeError(
        "Video qayta kodlanmadi."
    )


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "👋 Assalomu alaykum!\n\n"
        "Media yuklashni boshlash uchun uning havolasini yuboring.\n\n"
        "Qo'llab-quvvatlanadi:\n"
        "▶️ YouTube\n"
        "📸 Instagram\n"
        "🎵 TikTok\n"
        "📘 Facebook\n"
        "🟠 OK.ru\n"
        "📌 Pinterest\n"
        "👻 Snapchat\n"
        "❤️ Likee\n"
        "🧵 Threads\n\n"
        "🎤 Audio yoki ovoz yuborsangiz, "
        "Shazam orqali qo'shiqni aniqlayman."
    )

    await update.message.reply_text(text)


# ============================================================
# YOUTUBE MENU
# ============================================================

async def show_youtube_menu(
    update: Update,
    url: str
):

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎬 VIDEO",
                callback_data=f"ytvideo|{url}"
            ),
            InlineKeyboardButton(
                "🎵 MP3",
                callback_data=f"ytmp3|{url}"
            ),
        ]
    ])

    await update.message.reply_text(
        "YouTube uchun formatni tanlang:",
        reply_markup=keyboard,
    )


# ============================================================
# VIDEO DOWNLOAD
# ============================================================

async def process_video(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
):

    work = WORK_DIR / str(uuid.uuid4())
    work.mkdir(
        parents=True,
        exist_ok=True
    )

    source = None
    encoded = None

    try:

        await context.bot.send_chat_action(
            chat_id=chat_id,
            action=ChatAction.UPLOAD_VIDEO,
        )

        loop = asyncio.get_running_loop()

        source, info = await loop.run_in_executor(
            None,
            download_video_sync,
            url,
            work,
        )

        logger.info(
            "Source video: %s",
            source
        )

        if source.stat().st_size > MAX_SOURCE_BYTES:
            raise RuntimeError(
                "Manba video 1 GB dan katta."
            )

        encoded = work / "final_video.mp4"

        await loop.run_in_executor(
            None,
            encode_video_to_limit,
            source,
            encoded,
        )

        if not encoded.exists():
            raise RuntimeError(
                "Video fayli hosil bo'lmadi."
            )

        final_size = encoded.stat().st_size

        if final_size > MAX_VIDEO_BYTES:
            raise RuntimeError(
                "Tayyor video 14 MiB dan katta."
            )

        width, height = get_video_dimensions(
            encoded
        )

        if not width or not height:
            raise RuntimeError(
                "Tayyor video o'lchami aniqlanmadi."
            )

        # MP3 tugmasi faqat audio mavjud bo'lsa
        audio_exists = has_audio_track(source)

        job_id = uuid.uuid4().hex[:16]

        VIDEO_JOBS[job_id] = {
            "source": str(source),
            "url": url,
        }

        keyboard = None

        if audio_exists:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎵 MP3 yuklash",
                        callback_data=f"videoaudio|{job_id}"
                    )
                ]
            ])

        video_file = FSInputFile(
            str(encoded),
            filename="video.mp4"
        )

        await context.bot.send_video(
            chat_id=chat_id,
            video=video_file,
            caption="@yuklatgbot orqali yuklab olindi",
            reply_markup=keyboard,
            supports_streaming=True,
        )

    except Exception as e:

        logger.exception(
            "Video xatosi"
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Yuklashda xatolik.\n{e}"
        )

    finally:

        # source/encoded fayllar yuborilgandan keyin
        # papkani biroz kechiktirib o'chirish
        await asyncio.sleep(2)

        try:
            shutil.rmtree(
                work,
                ignore_errors=True
            )
        except Exception:
            pass


# ============================================================
# AUDIO FROM ORIGINAL VIDEO
# ============================================================

def extract_mp3_from_video_sync(
    source_path: Path,
    output_path: Path,
):
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),

        "-vn",

        "-c:a",
        "libmp3lame",

        "-b:a",
        "128k",

        str(output_path),
    ]

    result = run_cmd(
        cmd,
        timeout=600
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Videodan MP3 ajratib bo'lmadi."
        )

    if not output_path.exists():
        raise RuntimeError(
            "MP3 fayl hosil bo'lmadi."
        )

    return output_path


# ============================================================
# MP3 FROM URL
# ============================================================

async def process_mp3(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
):

    work = WORK_DIR / str(uuid.uuid4())
    work.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ Yuklanmoqda..."
        )

        loop = asyncio.get_running_loop()

        mp3 = await loop.run_in_executor(
            None,
            download_audio_sync,
            url,
            work,
        )

        audio_file = FSInputFile(
            str(mp3),
            filename="audio.mp3"
        )

        await context.bot.send_audio(
            chat_id=chat_id,
            audio=audio_file,
            caption="@yuklatgbot orqali yuklab olindi",
        )

    except Exception as e:

        logger.exception(
            "MP3 xatosi"
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ MP3 yuklashda xatolik.\n{e}"
        )

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True
        )


# ============================================================
# URL MESSAGE
# ============================================================

async def handle_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    text = update.message.text or ""

    url = extract_url(text)

    if not url:
        return

    site = detect_site(url)

    # YouTube oldindan tanlash
    if site == "youtube":

        await show_youtube_menu(
            update,
            url
        )

        return

    # boshqa barcha video saytlar
    await update.message.reply_text(
        "⏳ Yuklanmoqda..."
    )

    asyncio.create_task(
        process_video(
            update.effective_chat.id,
            context,
            url,
        )
    )


# ============================================================
# CALLBACK
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    data = query.data or ""

    # --------------------------------------------------------
    # YouTube VIDEO
    # --------------------------------------------------------

    if data.startswith("ytvideo|"):

        url = data.split(
            "|",
            1
        )[1]

        await query.edit_message_text(
            "⏳ Yuklanmoqda..."
        )

        asyncio.create_task(
            process_video(
                query.from_user.id,
                context,
                url,
            )
        )

        return

    # --------------------------------------------------------
    # YouTube MP3
    # --------------------------------------------------------

    if data.startswith("ytmp3|"):

        url = data.split(
            "|",
            1
        )[1]

        await query.edit_message_text(
            "⏳ Yuklanmoqda..."
        )

        asyncio.create_task(
            process_mp3(
                query.from_user.id,
                context,
                url,
            )
        )

        return

    # --------------------------------------------------------
    # Video ostidagi MP3
    # --------------------------------------------------------

    if data.startswith("videoaudio|"):

        job_id = data.split(
            "|",
            1
        )[1]

        job = VIDEO_JOBS.get(job_id)

        if not job:
            await query.message.reply_text(
                "❌ Bu video uchun ma'lumot topilmadi. "
                "Videoni qaytadan yuklang."
            )

            return

        source = Path(
            job["source"]
        )

        url = job["url"]

        # papka o'chib ketgan bo'lsa,
        # original URL'dan qayta yuklaymiz
        work = WORK_DIR / str(uuid.uuid4())
        work.mkdir(
            parents=True,
            exist_ok=True
        )

        try:

            await query.message.reply_text(
                "⏳ MP3 tayyorlanmoqda..."
            )

            if not source.exists():

                loop = asyncio.get_running_loop()

                source, _ = await loop.run_in_executor(
                    None,
                    download_video_sync,
                    url,
                    work,
                )

            mp3 = work / "audio.mp3"

            loop = asyncio.get_running_loop()

            await loop.run_in_executor(
                None,
                extract_mp3_from_video_sync,
                source,
                mp3,
            )

            audio_file = FSInputFile(
                str(mp3),
                filename="audio.mp3"
            )

            await context.bot.send_audio(
                chat_id=query.from_user.id,
                audio=audio_file,
                caption="@yuklatgbot orqali yuklab olindi",
            )

        except Exception as e:

            logger.exception(
                "Video MP3 callback xatosi"
            )

            await query.message.reply_text(
                f"❌ MP3 yuklashda xatolik.\n{e}"
            )

        finally:

            shutil.rmtree(
                work,
                ignore_errors=True
            )

        return


# ============================================================
# AUDIO / VOICE SHAZAM
# ============================================================

async def recognize_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    file_id: str,
    extension: str,
):

    work = WORK_DIR / str(uuid.uuid4())
    work.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        await update.message.reply_text(
            "🎤 Qo'shiq aniqlanmoqda..."
        )

        tg_file = await context.bot.get_file(
            file_id
        )

        input_file = work / f"input{extension}"

        await tg_file.download_to_drive(
            custom_path=str(input_file)
        )

        shazam = Shazam()

        result = await shazam.recognize(
            str(input_file)
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
            await update.message.reply_text(
                "❌ Qo'shiq aniqlanmadi."
            )

            return

        text = (
            "🎵 Qo'shiq topildi!\n\n"
            f"👤 Artist: {artist or 'Noma'lum'}\n"
            f"🎶 Qo'shiq: {title}"
        )

        # YouTube orqali MP3
        search_text = f"{artist or ''} {title}"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🎵 MP3",
                    callback_data=(
                        "songyt|" +
                        search_text[:180]
                    )
                )
            ]
        ])

        await update.message.reply_text(
            text,
            reply_markup=keyboard
        )

    except Exception as e:

        logger.exception(
            "Shazam xatosi"
        )

        await update.message.reply_text(
            "❌ Qo'shiqni aniqlab bo'lmadi."
        )

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True
        )


# ============================================================
# SONG YOUTUBE CALLBACK
# ============================================================

async def search_youtube_audio_sync(
    query: str,
    folder: Path,
):

    options = base_ydl_options(folder)

    options["format"] = "bestaudio/best"

    options["noplaylist"] = True

    options["default_search"] = "ytsearch1"

    options["postprocessors"] = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }
    ]

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.extract_info(
            f"ytsearch1:{query}",
            download=True
        )

    files = list(
        folder.glob("*.mp3")
    )

    if not files:
        raise RuntimeError(
            "Qo'shiq MP3 ko'rinishida topilmadi."
        )

    files.sort(
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    return files[0]


async def song_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    data = query.data or ""

    if not data.startswith("songyt|"):
        return

    search_text = data.split(
        "|",
        1
    )[1]

    work = WORK_DIR / str(uuid.uuid4())
    work.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        await query.message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        loop = asyncio.get_running_loop()

        mp3 = await loop.run_in_executor(
            None,
            search_youtube_audio_sync,
            search_text,
            work,
        )

        audio_file = FSInputFile(
            str(mp3),
            filename="song.mp3"
        )

        await context.bot.send_audio(
            chat_id=query.from_user.id,
            audio=audio_file,
            caption="@yuklatgbot orqali yuklab olindi",
        )

    except Exception as e:

        logger.exception(
            "Song MP3 xatosi"
        )

        await query.message.reply_text(
            f"❌ Qo'shiq yuklanmadi.\n{e}"
        )

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True
        )


# ============================================================
# AUDIO HANDLER
# ============================================================

async def audio_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.message.audio:

        await recognize_audio(
            update,
            context,
            update.message.audio.file_id,
            ".mp3",
        )

        return

    if update.message.voice:

        await recognize_audio(
            update,
            context,
            update.message.voice.file_id,
            ".ogg",
        )

        return


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.exception(
        "Unhandled exception",
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

    logger.info(
        "Telegram bot ishga tushmoqda..."
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
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
            callback_handler,
            pattern=r"^(ytvideo|ytmp3|videoaudio)\|"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            song_callback,
            pattern=r"^songyt\|"
        )
    )

    app.add_handler(
        MessageHandler(
            filters.AUDIO | filters.VOICE,
            audio_handler
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_url
        )
    )

    app.add_error_handler(
        error_handler
    )

    logger.info(
        "BOT ISHLAYAPTI"
    )

    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
