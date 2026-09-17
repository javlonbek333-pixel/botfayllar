import os
import re
import asyncio
import subprocess
import logging
from pathlib import Path

import yt_dlp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

DOWNLOAD_DIR = Path("/tmp/bot_downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# FINAL VIDEO LIMIT
# 14 MiB
TARGET_SIZE = 14 * 1024 * 1024

# SOURCE DOWNLOAD LIMIT
# 1 GB
MAX_SOURCE_SIZE = 1024 * 1024 * 1024

# Optional YouTube cookies
COOKIES_FILE = "/app/cookies.txt"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================
# URL HELPERS
# ============================================================

def is_url(text: str) -> bool:
    return bool(
        re.match(
            r"^https?://",
            text.strip(),
            re.IGNORECASE,
        )
    )


def is_youtube(url: str) -> bool:
    url = url.lower()

    return (
        "youtube.com" in url
        or "youtu.be" in url
    )


def is_supported_url(url: str) -> bool:

    supported = (
        "youtube.com",
        "youtu.be",

        "instagram.com",

        "tiktok.com",
        "vm.tiktok.com",

        "facebook.com",
        "fb.watch",

        "ok.ru",
        "odnoklassniki.ru",

        "pinterest.com",
        "pin.it",

        "snapchat.com",

        "likee.video",
        "likee.com",

        "threads.net",
    )

    url = url.lower()

    return any(
        domain in url
        for domain in supported
    )


# ============================================================
# COOKIES
# ============================================================

def cookie_options():

    if os.path.isfile(COOKIES_FILE):

        logger.info(
            "cookies.txt topildi"
        )

        return {
            "cookiefile": COOKIES_FILE
        }

    return {}


# ============================================================
# FILE HELPERS
# ============================================================

def clean_directory(
    directory: Path,
):

    try:

        for file in directory.iterdir():

            if file.is_file():

                try:
                    file.unlink()
                except Exception:
                    pass

    except Exception:
        pass


def newest_file(
    directory: Path,
    extensions,
):

    files = []

    for ext in extensions:

        files.extend(
            directory.glob(
                f"*.{ext}"
            )
        )

    if not files:
        return None

    files.sort(
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )

    return str(files[0])


# ============================================================
# COMMON YT-DLP OPTIONS
# ============================================================

def base_yt_options():

    options = {

        "noplaylist": True,

        "quiet": True,

        "no_warnings": True,

        "retries": 3,

        "fragment_retries": 3,

        "socket_timeout": 20,

        # Faster fragment downloading
        "concurrent_fragment_downloads": 8,

        "http_chunk_size":
            10 * 1024 * 1024,

        "restrictfilenames": True,

        # 1 GB SOURCE LIMIT
        "max_filesize":
            MAX_SOURCE_SIZE,
    }

    options.update(
        cookie_options()
    )

    return options


# ============================================================
# VIDEO DOWNLOAD
# ============================================================

def download_video(
    url: str,
    work_dir: Path,
):

    output_template = str(
        work_dir
        / "%(title).80s_%(id)s.%(ext)s"
    )

    options = base_yt_options()

    options.update({

        # Prefer <=480p.
        # Fallback if site does not offer it.
        "format": (
            "bestvideo[height<=480]+bestaudio/"
            "best[height<=480]/"
            "bestvideo+bestaudio/"
            "best"
        ),

        "outtmpl":
            output_template,

        "merge_output_format":
            "mp4",
    })

    logger.info(
        "VIDEO DOWNLOAD: %s",
        url,
    )

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=True,
        )

    file_path = newest_file(
        work_dir,
        [
            "mp4",
            "mkv",
            "webm",
            "mov",
            "avi",
        ],
    )

    if not file_path:

        raise RuntimeError(
            "Video yuklanmadi."
        )

    return file_path, info


# ============================================================
# MP3 DOWNLOAD
# ============================================================

def download_mp3(
    url: str,
    work_dir: Path,
):

    output_template = str(
        work_dir
        / "audio_source.%(ext)s"
    )

    options = base_yt_options()

    options.update({

        # AUDIO ONLY
        # Video yuklanmaydi.
        "format":
            "bestaudio[ext=m4a]/"
            "bestaudio/best",

        "outtmpl":
            output_template,

        "postprocessors": [],

        # Show logs internally,
        # user only sees "Yuklanmoqda..."
        "quiet": False,

        "no_warnings": False,
    })

    logger.info(
        "MP3: audio yuklanmoqda..."
    )

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=True,
        )

    source = newest_file(
        work_dir,
        [
            "m4a",
            "webm",
            "opus",
            "aac",
            "mp3",
        ],
    )

    if not source:

        raise RuntimeError(
            "Audio yuklanmadi."
        )

    output_file = str(
        work_dir
        / "audio_128kbps.mp3"
    )

    logger.info(
        "MP3: 128 kbps ga aylantirilmoqda..."
    )

    command = [

        "ffmpeg",

        "-y",

        "-i",
        source,

        "-vn",

        "-c:a",
        "libmp3lame",

        "-b:a",
        "128k",

        "-ar",
        "44100",

        "-ac",
        "2",

        output_file,
    ]

    result = subprocess.run(

        command,

        stdout=subprocess.PIPE,

        stderr=subprocess.PIPE,

        text=True,

        timeout=300,
    )

    if result.returncode != 0:

        logger.error(
            result.stderr[-5000:]
        )

        raise RuntimeError(
            "128 kbps MP3 yaratishda xatolik."
        )

    if not os.path.exists(
        output_file
    ):

        raise RuntimeError(
            "MP3 fayl yaratilmagan."
        )

    logger.info(
        "MP3 tayyor: %.2f MB",
        os.path.getsize(
            output_file
        ) / 1024 / 1024,
    )

    return output_file, info


# ============================================================
# VIDEO INFORMATION
# ============================================================

def get_duration(
    file_path: str,
) -> float:

    try:

        result = subprocess.run(

            [
                "ffprobe",
                "-v",
                "error",

                "-show_entries",
                "format=duration",

                "-of",
                "default="
                "noprint_wrappers=1:"
                "nokey=1",

                file_path,
            ],

            capture_output=True,

            text=True,

            timeout=30,
        )

        value = (
            result.stdout.strip()
        )

        if not value:
            return 0

        return float(value)

    except Exception as e:

        logger.error(
            "Duration error: %s",
            e,
        )

        return 0


def get_width(
    file_path: str,
) -> int:

    try:

        result = subprocess.run(

            [
                "ffprobe",
                "-v",
                "error",

                "-select_streams",
                "v:0",

                "-show_entries",
                "stream=width",

                "-of",
                "csv=p=0",

                file_path,
            ],

            capture_output=True,

            text=True,

            timeout=30,
        )

        value = (
            result.stdout.strip()
        )

        return int(value)

    except Exception:

        return 480


# ============================================================
# FFMPEG VIDEO ENCODE
# ============================================================

def encode_video(

    input_file: str,

    output_file: str,

    target_width: int,

    video_bitrate: int,

    audio_bitrate: int,
):

    source_width = get_width(
        input_file
    )

    # NEVER upscale
    width = min(
        target_width,
        source_width,
    )

    # Even number
    width = max(
        2,
        (width // 2) * 2,
    )

    command = [

        "ffmpeg",

        "-y",

        "-i",
        input_file,

        "-vf",
        f"scale={width}:-2",

        "-c:v",
        "libx264",

        # FAST ENCODING
        "-preset",
        "ultrafast",

        "-profile:v",
        "main",

        "-pix_fmt",
        "yuv420p",

        "-b:v",
        f"{video_bitrate}k",

        "-maxrate",
        f"{video_bitrate}k",

        "-bufsize",
        f"{video_bitrate * 2}k",

        "-c:a",
        "aac",

        "-b:a",
        f"{audio_bitrate}k",

        "-ac",
        "2",

        "-ar",
        "44100",

        "-movflags",
        "+faststart",

        output_file,
    ]

    result = subprocess.run(

        command,

        stdout=subprocess.PIPE,

        stderr=subprocess.PIPE,

        text=True,

        timeout=900,
    )

    if result.returncode != 0:

        logger.error(
            result.stderr[-5000:]
        )

        raise RuntimeError(
            "FFmpeg video xatosi."
        )


# ============================================================
# STRICT 14 MB VIDEO
# ============================================================

def compress_to_14mb(
    input_file: str,
) -> str:

    # IMPORTANT:
    # Even if the original video is below 14 MB,
    # it is STILL re-encoded.
    # Maximum resolution is 480p.

    duration = get_duration(
        input_file
    )

    if duration <= 0:

        raise RuntimeError(
            "Video davomiyligi aniqlanmadi."
        )

    source_width = get_width(
        input_file
    )

    max_width = min(
        480,
        source_width,
    )

    # Leave safety margin for MP4 overhead.
    total_kbps = max(
        70,
        int(
            (
                TARGET_SIZE
                * 8
                * 0.86
            )
            / duration
            / 1000
        ),
    )

    candidates = []

    for width in (
        480,
        426,
        360,
        320,
    ):

        if width > max_width:
            continue

        if width >= 426:
            audio_bitrate = 48
        else:
            audio_bitrate = 32

        video_bitrate = max(
            40,
            total_kbps
            - audio_bitrate,
        )

        # Several bitrate attempts.
        for factor in (
            1.00,
            0.82,
            0.65,
            0.50,
        ):

            candidates.append(
                (
                    width,
                    max(
                        40,
                        int(
                            video_bitrate
                            * factor
                        ),
                    ),
                    audio_bitrate,
                )
            )

    # Emergency fallbacks
    candidates.extend([
        (320, 65, 32),
        (320, 50, 32),
        (320, 45, 32),
    ])

    tried = set()

    for index, (
        width,
        video_bitrate,
        audio_bitrate,
    ) in enumerate(
        candidates,
        start=1,
    ):

        key = (
            width,
            video_bitrate,
            audio_bitrate,
        )

        if key in tried:
            continue

        tried.add(key)

        output_file = str(

            DOWNLOAD_DIR
            /
            (
                f"encoded_"
                f"{os.getpid()}_"
                f"{index}_"
                f"{width}p.mp4"
            )
        )

        logger.info(

            "Encode %s: "
            "%sp "
            "%sk video "
            "%sk audio",

            index,
            width,
            video_bitrate,
            audio_bitrate,
        )

        try:

            encode_video(

                input_file,

                output_file,

                width,

                video_bitrate,

                audio_bitrate,
            )

            if not os.path.exists(
                output_file
            ):
                continue

            size = os.path.getsize(
                output_file
            )

            logger.info(
                "Encoded: %.2f MB",
                size / 1024 / 1024,
            )

            # STRICT LIMIT
            if size <= TARGET_SIZE:

                logger.info(
                    "SUCCESS: <=14 MB"
                )

                return output_file

            try:
                os.remove(
                    output_file
                )
            except Exception:
                pass

        except Exception as e:

            logger.error(
                "Encode failed: %s",
                e,
            )

            try:

                if os.path.exists(
                    output_file
                ):
                    os.remove(
                        output_file
                    )

            except Exception:
                pass

    raise RuntimeError(
        "Videoni 14 MB dan kichik "
        "qilishning iloji bo'lmadi."
    )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(

        """👋 Assalomu aleykum

📥 MEDIA YUKLASH

• YouTube — video, Shorts, audio
• Instagram — post, reels
• TikTok — video
• Facebook — reels, video
• OK.ru — video
• Pinterest — rasm, video
• Snapchat — rasm, video
• Likee — rasm, video
• Threads — rasm, video

🎬 Video — 480p gacha
📦 Video — 14 MB dan oshmaydi
🎵 MP3 — 128 kbps

🚀 Havolani yuboring.

🔎 Qo'shiq qidirish uchun
qo'shiq nomi yoki ijrochini yozing."""
    )


# ============================================================
# VIDEO PROCESS
# ============================================================

async def process_video(

    update_or_query,

    context,

    url,

    status_message=None,
):

    # CallbackQuery.message OR normal Message
    message = update_or_query.message

    status = status_message

    if status is None:

        status = await message.reply_text(
            "⏳ Yuklanmoqda..."
        )

    # IMPORTANT:
    # CallbackQuery uses from_user, NOT effective_user.
    user_id = update_or_query.from_user.id

    work_dir = (
        DOWNLOAD_DIR
        / str(user_id)
    )

    work_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        file_path, info = (
            await asyncio.to_thread(
                download_video,
                url,
                work_dir,
            )
        )

        # ALWAYS re-encode to <=480p
        # even if source is below 14 MB.
        final_file = (
            await asyncio.to_thread(
                compress_to_14mb,
                file_path,
            )
        )

        final_size = (
            os.path.getsize(
                final_file
            )
        )

        if final_size > TARGET_SIZE:

            raise RuntimeError(
                "Yakuniy video 14 MB dan katta."
            )

        with open(
            final_file,
            "rb",
        ) as video:

            await message.reply_video(

                video=video,

                supports_streaming=True,

                read_timeout=180,

                write_timeout=180,

                connect_timeout=60,

                pool_timeout=180,
            )

        try:
            await status.delete()
        except Exception:
            pass

    except Exception as e:

        logger.exception(
            "VIDEO ERROR"
        )

        try:

            await status.edit_text(
                "❌ Xatolik:\n\n"
                + str(e)[-1800:]
            )

        except Exception:
            pass

    finally:

        await asyncio.to_thread(
            clean_directory,
            work_dir,
        )


# ============================================================
# MP3 PROCESS
# ============================================================

async def process_mp3(

    update_or_query,

    context,

    url,

    status_message=None,
):

    message = update_or_query.message

    status = status_message

    if status is None:

        status = await message.reply_text(
            "⏳ Yuklanmoqda..."
        )

    # IMPORTANT:
    # CallbackQuery -> from_user.id
    user_id = update_or_query.from_user.id

    work_dir = (
        DOWNLOAD_DIR
        / str(user_id)
    )

    work_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        audio_file, info = (
            await asyncio.to_thread(
                download_mp3,
                url,
                work_dir,
            )
        )

        with open(
            audio_file,
            "rb",
        ) as audio:

            await message.reply_audio(

                audio=audio,

                title=(
                    info.get("title")
                    or "Audio"
                )[:64],

                performer=(
                    info.get("uploader")
                    or ""
                )[:64],

                read_timeout=180,

                write_timeout=180,

                connect_timeout=60,

                pool_timeout=180,
            )

        try:
            await status.delete()
        except Exception:
            pass

    except Exception as e:

        logger.exception(
            "MP3 ERROR"
        )

        try:

            await status.edit_text(
                "❌ Xatolik:\n\n"
                + str(e)[-1800:]
            )

        except Exception:
            pass

    finally:

        await asyncio.to_thread(
            clean_directory,
            work_dir,
        )


# ============================================================
# YOUTUBE URL
# ============================================================

async def handle_url(

    update: Update,

    context: ContextTypes.DEFAULT_TYPE,
):

    url = update.message.text.strip()

    if not is_supported_url(url):

        await update.message.reply_text(
            "❌ Bu sayt "
            "qo'llab-quvvatlanmaydi."
        )

        return

    # YouTube -> buttons
    if is_youtube(url):

        # Save URL for this user.
        context.user_data[
            "youtube_url"
        ] = url

        keyboard = InlineKeyboardMarkup([

            [

                InlineKeyboardButton(

                    "🎬 VIDEO 480p",

                    callback_data=
                    "youtube_video",
                ),

                InlineKeyboardButton(

                    "🎵 MP3 128 kbps",

                    callback_data=
                    "youtube_mp3",
                ),

            ]

        ])

        await update.message.reply_text(

            "YouTube havolasi qabul qilindi.\n"
            "Kerakli formatni tanlang:",

            reply_markup=keyboard,
        )

        return

    # Other supported websites
    await process_video(
        update,
        context,
        url,
    )


# ============================================================
# SEARCH 50 SONGS
# ============================================================

def search_youtube_50(
    query: str,
):

    options = {

        "quiet": True,

        "no_warnings": True,

        "extract_flat": True,

        "playlistend": 50,

        "default_search":
            "ytsearch50",

        "noplaylist": False,
    }

    options.update(
        cookie_options()
    )

    logger.info(
        "Searching: %s",
        query,
    )

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        result = ydl.extract_info(

            "ytsearch50:"
            + query,

            download=False,
        )

    results = []

    entries = (
        result.get("entries")
        or []
    )

    for entry in entries[:50]:

        if not entry:
            continue

        video_id = entry.get(
            "id"
        )

        title = (
            entry.get("title")
            or "Noma'lum"
        )

        url = entry.get(
            "webpage_url"
        )

        if not url and video_id:

            url = (
                "https://www.youtube.com/watch?v="
                + video_id
            )

        if url:

            results.append({

                "title": title,

                "url": url,
            })

    return results[:50]


# ============================================================
# SEARCH KEYBOARD
# ============================================================

def build_search_keyboard(

    results,

    page,
):

    # 10 RESULTS PER PAGE
    per_page = 10

    start = page * per_page

    end = min(
        start + per_page,
        len(results),
    )

    rows = []

    for index in range(
        start,
        end,
    ):

        number = index + 1

        title = (
            results[index]["title"]
        )

        if len(title) > 45:

            title = (
                title[:42]
                + "..."
            )

        rows.append([

            InlineKeyboardButton(

                f"{number}. {title}",

                callback_data=
                f"song|{number}",
            )

        ])

    navigation = []

    # Previous
    if page > 0:

        navigation.append(

            InlineKeyboardButton(

                "⬅️ Oldingi",

                callback_data=
                f"page|{page - 1}",
            )
        )

    # Next
    if end < len(results):

        navigation.append(

            InlineKeyboardButton(

                "Keyingi ➡️",

                callback_data=
                f"page|{page + 1}",
            )
        )

    if navigation:

        rows.append(
            navigation
        )

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# SONG SEARCH
# ============================================================

async def handle_search(

    update: Update,

    context: ContextTypes.DEFAULT_TYPE,
):

    search_text = (
        update.message.text.strip()
    )

    status = (
        await update.message.reply_text(
            "⏳ Yuklanmoqda..."
        )
    )

    try:

        results = (
            await asyncio.to_thread(
                search_youtube_50,
                search_text,
            )
        )

        if not results:

            await status.edit_text(
                "❌ Qo'shiq topilmadi."
            )

            return

        # Save all 50 results.
        context.user_data[
            "search_results"
        ] = results

        # First page
        page = 0

        keyboard = (
            build_search_keyboard(
                results,
                page,
            )
        )

        await status.edit_text(

            "🎵 50 ta natija\n"
            "📄 1 / 5-sahifa\n\n"
            "Kerakli qo'shiqni tanlang:",

            reply_markup=keyboard,
        )

    except Exception as e:

        logger.exception(
            "SEARCH ERROR"
        )

        await status.edit_text(

            "❌ Qidirishda xatolik:\n\n"
            + str(e)[-1800:]
        )


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def callback_handler(

    update: Update,

    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    data = query.data

    # ========================================================
    # YOUTUBE VIDEO
    # ========================================================

    if data == "youtube_video":

        url = context.user_data.get(
            "youtube_url"
        )

        if not url:

            await query.message.reply_text(

                "❌ Havola eskirgan.\n"
                "YouTube havolasini qayta yuboring."
            )

            return

        await query.edit_message_text(
            "⏳ Yuklanmoqda..."
        )

        await process_video(

            query,

            context,

            url,

            status_message=
            query.message,
        )

        return

    # ========================================================
    # YOUTUBE MP3
    # ========================================================

    if data == "youtube_mp3":

        url = context.user_data.get(
            "youtube_url"
        )

        if not url:

            await query.message.reply_text(

                "❌ Havola eskirgan.\n"
                "YouTube havolasini qayta yuboring."
            )

            return

        await query.edit_message_text(
            "⏳ Yuklanmoqda..."
        )

        await process_mp3(

            query,

            context,

            url,

            status_message=
            query.message,
        )

        return

    # ========================================================
    # SEARCH PAGE
    # ========================================================

    if data.startswith(
        "page|"
    ):

        try:

            page = int(
                data.split("|")[1]
            )

        except Exception:

            return

        results = (
            context.user_data.get(
                "search_results",
                [],
            )
        )

        if not results:

            await query.message.reply_text(

                "❌ Qidiruv eskirgan.\n"
                "Qaytadan qidiring."
            )

            return

        keyboard = (
            build_search_keyboard(
                results,
                page,
            )
        )

        await query.edit_message_text(

            f"🎵 50 ta natija\n"
            f"📄 {page + 1} / 5-sahifa\n\n"
            f"Kerakli qo'shiqni tanlang:",

            reply_markup=keyboard,
        )

        return

    # ========================================================
    # SONG SELECT
    # ========================================================

    if data.startswith(
        "song|"
    ):

        try:

            number = int(
                data.split("|")[1]
            )

        except Exception:

            return

        results = (
            context.user_data.get(
                "search_results",
                [],
            )
        )

        if (
            number < 1
            or number > len(results)
        ):

            await query.message.reply_text(

                "❌ Natija topilmadi.\n"
                "Qaytadan qidiring."
            )

            return

        url = results[
            number - 1
        ]["url"]

        await query.edit_message_text(
            "⏳ Yuklanmoqda..."
        )

        await process_mp3(

            query,

            context,

            url,

            status_message=
            query.message,
        )

        return


# ============================================================
# TEXT HANDLER
# ============================================================

async def text_handler(

    update: Update,

    context: ContextTypes.DEFAULT_TYPE,
):

    text = update.message.text.strip()

    if is_url(text):

        await handle_url(
            update,
            context,
        )

    else:

        # Normal text = song search
        await handle_search(
            update,
            context,
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(

    update: object,

    context: ContextTypes.DEFAULT_TYPE,
):

    logger.exception(

        "Unhandled exception",

        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(

            "BOT_TOKEN Railway Variables "
            "ichida topilmadi!"
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # /start
    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    # Buttons
    app.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # Text
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            text_handler,
        )
    )

    app.add_error_handler(
        error_handler
    )

    logger.info(
        "BOT ISHLAYAPTI"
    )

    app.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
