import os
import re
import uuid
import shutil
import logging
import asyncio
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# =========================================================
# SOZLAMALAR
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")

DOWNLOAD_DIR = "/tmp/bot_downloads"

MAX_FILE_SIZE_MB = 49
MAX_SEARCH_RESULTS = 10

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# QO'LLAB-QUVVATLANADIGAN SAYTLAR
# =========================================================

SUPPORTED_DOMAINS = [
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",

    "instagram.com",

    "tiktok.com",

    "facebook.com",
    "fb.watch",

    "pinterest.com",
    "pin.it",

    "snapchat.com",

    "likee.video",
    "likee.com",

    "threads.net",
]


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "👋 <b>Assalomu aleykum!</b>\n\n"

        "📥 <b>Quyidagilarni yuklab olishingiz mumkin:</b>\n\n"

        "• Instagram — post, stories, reels\n"
        "• YouTube — video, Shorts, audio\n"
        "• TikTok — video\n"
        "• Facebook — reels, video\n"
        "• Pinterest — rasm, video\n"
        "• Snapchat — rasm, video\n"
        "• Likee — rasm, video\n"
        "• Threads — rasm, video\n\n"

        "🎵 <b>Musiqa qidirish:</b>\n"
        "Qo'shiq nomi yoki ijrochini yozing.\n\n"

        "🔗 <b>Yuklash:</b>\n"
        "Havolani yuboring."
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# =========================================================
# URL TOPISH
# =========================================================

def extract_url(text):

    pattern = r"https?://[^\s]+"

    match = re.search(pattern, text)

    if not match:
        return None

    url = match.group(0)

    url = url.rstrip(
        ".,!?)]}>\"'"
    )

    return url


# =========================================================
# URL TEKSHIRISH
# =========================================================

def is_supported_url(url):

    try:

        parsed = urlparse(url)

        if parsed.scheme not in (
            "http",
            "https"
        ):
            return False

        host = parsed.netloc.lower()

        if host.startswith("www."):
            host = host[4:]

        for domain in SUPPORTED_DOMAINS:

            if (
                host == domain
                or host.endswith("." + domain)
            ):
                return True

        return False

    except Exception:

        return False


# =========================================================
# YOUTUBE URL
# =========================================================

def is_youtube_url(url):

    try:

        host = urlparse(url).netloc.lower()

        return (
            "youtube.com" in host
            or "youtu.be" in host
        )

    except Exception:

        return False


# =========================================================
# VAQT FORMAT
# =========================================================

def format_time(seconds):

    if not seconds:
        return "0:00"

    try:

        seconds = int(seconds)

        minutes, seconds = divmod(
            seconds,
            60
        )

        return f"{minutes}:{seconds:02d}"

    except Exception:

        return "0:00"


# =========================================================
# FAYL NOMINI TOZALASH
# =========================================================

def clean_filename(name):

    name = re.sub(
        r'[\\/*?:"<>|]',
        "",
        name
    )

    name = name.strip()

    if not name:
        name = "download"

    return name[:100]


# =========================================================
# FAYL HAJMI
# =========================================================

def get_size_mb(path):

    if not os.path.exists(path):
        return 0

    return (
        os.path.getsize(path)
        / 1024
        / 1024
    )


# =========================================================
# FFmpeg TEKSHIRISH
# =========================================================

def check_ffmpeg():

    path = shutil.which("ffmpeg")

    if path:

        logger.info(
            "FFmpeg topildi: %s",
            path
        )

        return True

    logger.error(
        "FFmpeg TOPILMADI!"
    )

    return False


# =========================================================
# VIDEO SIQISH
# =========================================================

def compress_to_480p(
    input_path,
    output_path
):

    logger.info(
        "Video siqilmoqda: %s",
        input_path
    )

    cmd = [
        "ffmpeg",
        "-y",

        "-i",
        input_path,

        "-vf",
        "scale=-2:480",

        "-c:v",
        "libx264",

        "-crf",
        "28",

        "-preset",
        "veryfast",

        "-c:a",
        "aac",

        "-b:a",
        "96k",

        "-movflags",
        "+faststart",

        output_path,
    ]

    try:

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:

            logger.error(
                "FFmpeg xatosi:\n%s",
                result.stderr[-5000:]
            )

            return False

        if (
            os.path.exists(output_path)
            and os.path.getsize(output_path) > 0
        ):

            logger.info(
                "Siqilgan video: %.2f MB",
                get_size_mb(output_path)
            )

            return True

    except Exception as e:

        logger.exception(
            "FFmpeg exception: %s",
            e
        )

    return False


# =========================================================
# YOUTUBE QIDIRISH
# =========================================================

def search_youtube(query):

    logger.info(
        "YouTube qidiruv: %s",
        query
    )

    ydl_opts = {

        "quiet": True,

        "no_warnings": False,

        "extract_flat": True,

        "skip_download": True,

        "nocheckcertificate": True,

    }

    try:

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                f"ytsearch{MAX_SEARCH_RESULTS}:{query}",
                download=False
            )

        results = []

        for entry in info.get(
            "entries",
            []
        ):

            if not entry:
                continue

            video_id = entry.get("id")

            if not video_id:
                continue

            results.append({

                "id": video_id,

                "title": entry.get(
                    "title",
                    "Noma'lum"
                ),

                "duration": entry.get(
                    "duration",
                    0
                ),

                "url":
                    "https://www.youtube.com/watch?v="
                    + video_id
            })

        logger.info(
            "Topilgan natijalar: %d",
            len(results)
        )

        return results

    except Exception as e:

        logger.exception(
            "YouTube qidiruv xatosi: %s",
            e
        )

        return []


# =========================================================
# QIDIRUV TUGMALARI
# =========================================================

def create_search_keyboard(
    results,
    context,
    message_id
):

    buttons = []

    for index, entry in enumerate(
        results[:10],
        start=1
    ):

        title = entry["title"]

        duration = format_time(
            entry["duration"]
        )

        if len(title) > 55:

            title = title[:52] + "..."

        text = (
            f"{index}. {title} "
            f"[{duration}]"
        )

        song_key = (
            f"{message_id}_{index}"
        )

        if "search_results" not in context.user_data:

            context.user_data[
                "search_results"
            ] = {}

        context.user_data[
            "search_results"
        ][song_key] = {

            "url": entry["url"],

            "title": entry["title"],
        }

        buttons.append([
            InlineKeyboardButton(
                text=text,
                callback_data=f"song:{song_key}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="❌ Yopish",
            callback_data="cancel"
        )
    ])

    return InlineKeyboardMarkup(
        buttons
    )


# =========================================================
# YOUTUBE FORMAT TUGMALARI
# =========================================================

def youtube_format_keyboard(
    key
):

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎬 Video",
                    callback_data=f"yvideo:{key}"
                ),

                InlineKeyboardButton(
                    "🎵 MP3",
                    callback_data=f"yaudio:{key}"
                )
            ],

            [
                InlineKeyboardButton(
                    "❌ Bekor qilish",
                    callback_data="cancel"
                )
            ]
        ]
    )


# =========================================================
# MEDIA YUKLASH
# =========================================================

def download_video(
    url,
    work_dir
):

    os.makedirs(
        work_dir,
        exist_ok=True
    )

    output_template = os.path.join(
        work_dir,
        "%(id)s.%(ext)s"
    )

    ydl_opts = {

        # 720p gacha
        "format":
            "bestvideo[height<=720]+"
            "bestaudio/"
            "best[height<=720]/"
            "best",

        "outtmpl":
            output_template,

        "merge_output_format":
            "mp4",

        "noplaylist":
            True,

        "quiet":
            False,

        "no_warnings":
            False,

        "nocheckcertificate":
            True,

        # YouTube JS challenge
        "js_runtimes": {
            "deno": {}
        },

        "prefer_ffmpeg":
            True,

        "retries":
            3,

        "fragment_retries":
            3,

        "continuedl":
            True,

        "overwrites":
            True,
    }

    logger.info(
        "Video yuklanmoqda: %s",
        url
    )

    with yt_dlp.YoutubeDL(
        ydl_opts
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        title = info.get(
            "title",
            "Video"
        )

        video_id = info.get(
            "id"
        )

    if not video_id:

        raise RuntimeError(
            "Video ID olinmadi."
        )

    # Fayllarni topamiz
    files = []

    for filename in os.listdir(
        work_dir
    ):

        if filename.endswith(
            ".part"
        ):
            continue

        if filename.endswith(
            ".ytdl"
        ):
            continue

        full_path = os.path.join(
            work_dir,
            filename
        )

        if os.path.isfile(
            full_path
        ):

            files.append(
                full_path
            )

    if not files:

        raise FileNotFoundError(
            "Yuklangan fayl topilmadi."
        )

    # MP4 ni afzal ko'ramiz
    mp4_files = [
        f for f in files
        if f.lower().endswith(
            ".mp4"
        )
    ]

    if mp4_files:

        file_path = max(
            mp4_files,
            key=os.path.getsize
        )

    else:

        file_path = max(
            files,
            key=os.path.getsize
        )

    logger.info(
        "Video tayyor: %s | %.2f MB",
        file_path,
        get_size_mb(file_path)
    )

    return file_path, title


# =========================================================
# AUDIO YUKLASH
# =========================================================

def download_audio(
    url,
    work_dir
):

    os.makedirs(
        work_dir,
        exist_ok=True
    )

    output_template = os.path.join(
        work_dir,
        "%(id)s.%(ext)s"
    )

    ydl_opts = {

        "format":
            "bestaudio/best",

        "outtmpl":
            output_template,

        "noplaylist":
            True,

        "quiet":
            False,

        "no_warnings":
            False,

        "nocheckcertificate":
            True,

        # YouTube JS challenge
        "js_runtimes": {
            "deno": {}
        },

        "prefer_ffmpeg":
            True,

        "retries":
            3,

        "fragment_retries":
            3,

        "postprocessors": [

            {
                "key":
                    "FFmpegExtractAudio",

                "preferredcodec":
                    "mp3",

                "preferredquality":
                    "192",
            }
        ],
    }

    logger.info(
        "Audio yuklanmoqda: %s",
        url
    )

    with yt_dlp.YoutubeDL(
        ydl_opts
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        title = info.get(
            "title",
            "Musiqa"
        )

        video_id = info.get(
            "id"
        )

    if not video_id:

        raise RuntimeError(
            "Video ID olinmadi."
        )

    mp3_path = os.path.join(
        work_dir,
        video_id + ".mp3"
    )

    # To'g'ridan-to'g'ri topilmasa
    if not os.path.exists(
        mp3_path
    ):

        mp3_files = []

        for filename in os.listdir(
            work_dir
        ):

            if filename.lower().endswith(
                ".mp3"
            ):

                mp3_files.append(
                    os.path.join(
                        work_dir,
                        filename
                    )
                )

        if mp3_files:

            mp3_path = max(
                mp3_files,
                key=os.path.getsize
            )

    if not os.path.exists(
        mp3_path
    ):

        raise FileNotFoundError(
            "MP3 fayli yaratilmadi."
        )

    logger.info(
        "MP3 tayyor: %s | %.2f MB",
        mp3_path,
        get_size_mb(mp3_path)
    )

    return mp3_path, title


# =========================================================
# FAYLNI TELEGRAMGA YUBORISH
# =========================================================

async def send_downloaded_file(
    message,
    file_path,
    title,
    mode
):

    if not os.path.exists(
        file_path
    ):

        raise FileNotFoundError(
            "Fayl mavjud emas."
        )

    size_mb = get_size_mb(
        file_path
    )

    logger.info(
        "Telegramga yuborish: %.2f MB",
        size_mb
    )

    if size_mb > MAX_FILE_SIZE_MB:

        raise RuntimeError(
            f"Fayl juda katta: "
            f"{size_mb:.1f} MB. "
            f"Limit: {MAX_FILE_SIZE_MB} MB."
        )

    filename = clean_filename(
        title
    )

    # =====================================================
    # AUDIO
    # =====================================================

    if mode == "audio":

        from telegram import InputFile

        with open(
            file_path,
            "rb"
        ) as audio:

            await message.reply_audio(
                audio=audio,
                title=title[:64],
                caption="@yuklatgbot orqali yuklab olindi"
            )

        return

    # =====================================================
    # VIDEO / IMAGE
    # =====================================================

    extension = Path(
        file_path
    ).suffix.lower()

    # RASM
    if extension in [
        ".jpg",
        ".jpeg",
        ".png",
        ".webp"
    ]:

        with open(
            file_path,
            "rb"
        ) as photo:

            await message.reply_photo(
                photo=photo,
                caption=title[:1024]
            )

        return

    # VIDEO
    if extension in [
        ".mp4",
        ".mov",
        ".mkv",
        ".webm"
    ]:

        with open(
            file_path,
            "rb"
        ) as video:

            await message.reply_video(
                video=video,
                caption="@yuklatgbot orqali yuklab olindi",
                supports_streaming=True
            )

        return

    # Boshqa format
    with open(
        file_path,
        "rb"
    ) as document:

        await message.reply_document(
            document=document,
            caption=title[:1024]
        )


# =========================================================
# URL YUKLASH
# =========================================================

async def process_video_download(
    message,
    url
):

    status = await message.reply_text(
        "⏳ <b>Yuklanmoqda...</b>\n\n"
        "Biroz kuting.",
        parse_mode="HTML"
    )

    job_id = str(
        uuid.uuid4()
    )

    work_dir = os.path.join(
        DOWNLOAD_DIR,
        job_id
    )

    os.makedirs(
        work_dir,
        exist_ok=True
    )

    raw_file = None
    compressed_file = os.path.join(
        work_dir,
        "compressed.mp4"
    )

    try:

        raw_file, title = await asyncio.to_thread(
            download_video,
            url,
            work_dir
        )

        logger.info(
            "Asl video: %.2f MB",
            get_size_mb(raw_file)
        )

        # =================================================
        # VIDEO SIQISH
        # =================================================

        final_file = raw_file

        extension = Path(
            raw_file
        ).suffix.lower()

        # Faqat videoni siqamiz
        if extension in [
            ".mp4",
            ".mov",
            ".mkv",
            ".webm"
        ]:

            compressed = await asyncio.to_thread(
                compress_to_480p,
                raw_file,
                compressed_file
            )

            if compressed:

                final_file = compressed_file

        # =================================================
        # TELEGRAM
        # =================================================

        await send_downloaded_file(
            message,
            final_file,
            title,
            "video"
        )

        try:
            await status.delete()
        except Exception:
            pass

    except Exception as e:

        logger.exception(
            "MEDIA YUKLASH XATOSI"
        )

        error_text = str(e)

        if len(error_text) > 1500:
            error_text = error_text[-1500:]

        try:

            await status.edit_text(
                "❌ <b>Yuklashda xatolik:</b>\n\n"
                f"<code>{error_text}</code>",
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
# AUDIO YUKLASH
# =========================================================

async def process_audio_download(
    message,
    url
):

    status = await message.reply_text(
        "⏳ <b>MP3 tayyorlanmoqda...</b>\n\n"
        "Biroz kuting.",
        parse_mode="HTML"
    )

    job_id = str(
        uuid.uuid4()
    )

    work_dir = os.path.join(
        DOWNLOAD_DIR,
        job_id
    )

    os.makedirs(
        work_dir,
        exist_ok=True
    )

    try:

        audio_file, title = await asyncio.to_thread(
            download_audio,
            url,
            work_dir
        )

        await send_downloaded_file(
            message,
            audio_file,
            title,
            "audio"
        )

        try:
            await status.delete()
        except Exception:
            pass

    except Exception as e:

        logger.exception(
            "AUDIO YUKLASH XATOSI"
        )

        error_text = str(e)

        if len(error_text) > 1500:
            error_text = error_text[-1500:]

        try:

            await status.edit_text(
                "❌ <b>MP3 yuklashda xatolik:</b>\n\n"
                f"<code>{error_text}</code>",
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
# ASOSIY MATN HANDLER
# =========================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    text = update.message.text.strip()

    if not text:
        return

    # =====================================================
    # URL
    # =====================================================

    url = extract_url(text)

    if url:

        if not is_supported_url(url):

            await update.message.reply_text(
                "❌ Bu sayt hozircha qo'llab-quvvatlanmaydi.\n\n"

                "Qo'llab-quvvatlanadigan saytlar:\n"
                "• Instagram\n"
                "• YouTube\n"
                "• TikTok\n"
                "• Facebook\n"
                "• Pinterest\n"
                "• Snapchat\n"
                "• Likee\n"
                "• Threads"
            )

            return

        # =================================================
        # YOUTUBE
        # =================================================

        if is_youtube_url(url):

            key = str(
                uuid.uuid4()
            )

            if "url_results" not in context.user_data:

                context.user_data[
                    "url_results"
                ] = {}

            context.user_data[
                "url_results"
            ][key] = url

            await update.message.reply_text(
                "🎬 <b>YouTube</b>\n\n"
                "Qaysi format kerak?",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🎬 Video",
                                callback_data=f"urlvideo:{key}"
                            ),

                            InlineKeyboardButton(
                                "🎵 MP3",
                                callback_data=f"urlaudio:{key}"
                            )
                        ]
                    ]
                ),
                parse_mode="HTML"
            )

            return

        # =================================================
        # INSTAGRAM / TIKTOK / FACEBOOK / ...
        # =================================================

        await process_video_download(
            update.message,
            url
        )

        return

    # =====================================================
    # QO'SHIQ QIDIRISH
    # =====================================================

    status = await update.message.reply_text(
        "🔍 <b>YouTube'dan qidirilmoqda...</b>",
        parse_mode="HTML"
    )

    try:

        results = await asyncio.to_thread(
            search_youtube,
            text
        )

        if not results:

            await status.edit_text(
                "❌ Hech qanday natija topilmadi."
            )

            return

        message_text = (
            f"🎧 <b>{text}</b>\n\n"
            "Kerakli qo'shiqni tanlang:"
        )

        keyboard = create_search_keyboard(
            results,
            context,
            update.message.message_id
        )

        await status.edit_text(
            message_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:

        logger.exception(
            "QIDIRUV XATOSI"
        )

        await status.edit_text(
            "❌ Qidirishda xatolik yuz berdi."
        )


# =========================================================
# CALLBACK
# =========================================================

async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    # =====================================================
    # BEKOR QILISH
    # =====================================================

    if data == "cancel":

        try:
            await query.message.delete()
        except Exception:
            pass

        return

    # =====================================================
    # QIDIRUV NATIJASI TANLANDI
    # =====================================================

    if data.startswith("song:"):

        key = data.split(
            "song:",
            1
        )[1]

        search_results = context.user_data.get(
            "search_results",
            {}
        )

        song = search_results.get(
            key
        )

        if not song:

            await query.message.reply_text(
                "❌ Bu qidiruv natijasi eskirgan.\n"
                "Qo'shiqni qaytadan qidiring."
            )

            return

        # YouTube URL saqlanadi
        url_key = str(
            uuid.uuid4()
        )

        if "url_results" not in context.user_data:

            context.user_data[
                "url_results"
            ] = {}

        context.user_data[
            "url_results"
        ][url_key] = song["url"]

        await query.message.reply_text(
            f"🎵 <b>{song['title'][:100]}</b>\n\n"
            "Qaysi format kerak?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🎬 Video",
                            callback_data=f"urlvideo:{url_key}"
                        ),

                        InlineKeyboardButton(
                            "🎵 MP3",
                            callback_data=f"urlaudio:{url_key}"
                        )
                    ]
                ]
            ),
            parse_mode="HTML"
        )

        return

    # =====================================================
    # URL -> VIDEO
    # =====================================================

    if data.startswith(
        "urlvideo:"
    ):

        key = data.split(
            "urlvideo:",
            1
        )[1]

        urls = context.user_data.get(
            "url_results",
            {}
        )

        url = urls.get(
            key
        )

        if not url:

            await query.message.reply_text(
                "❌ Havola eskirgan. "
                "Qaytadan yuboring."
            )

            return

        await query.answer(
            "Video yuklanmoqda..."
        )

        await process_video_download(
            query.message,
            url
        )

        return

    # =====================================================
    # URL -> AUDIO
    # =====================================================

    if data.startswith(
        "urlaudio:"
    ):

        key = data.split(
            "urlaudio:",
            1
        )[1]

        urls = context.user_data.get(
            "url_results",
            {}
        )

        url = urls.get(
            key
        )

        if not url:

            await query.message.reply_text(
                "❌ Havola eskirgan."
            )

            return

        await query.answer(
            "MP3 tayyorlanmoqda..."
        )

        await process_audio_download(
            query.message,
            url
        )

        return


# =========================================================
# BOTNI ISHGA TUSHIRISH
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN Railway Variables'da "
            "topilmadi!"
        )

    logger.info(
        "===================================="
    )

    logger.info(
        "Universal Downloader Bot"
    )

    logger.info(
        "===================================="
    )

    logger.info(
        "yt-dlp version: %s",
        yt_dlp.version.__version__
    )

    check_ffmpeg()

    deno = shutil.which(
        "deno"
    )

    if deno:

        logger.info(
            "Deno topildi: %s",
            deno
        )

    else:

        logger.warning(
            "Deno topilmadi! "
            "YouTube yuklashda muammo bo'lishi mumkin."
        )

    app = (
        ApplicationBuilder()
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
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            handle_callback
        )
    )

    logger.info(
        "BOT ISHLAYAPTI..."
    )

    app.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
