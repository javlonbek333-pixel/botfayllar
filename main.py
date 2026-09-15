import os
import re
import uuid
import shutil
import logging
import requests

import yt_dlp
from shazamio import Shazam

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

# Video uchun maqsadli limit
TARGET_SIZE_MB = 14

FFMPEG = "/usr/bin/ffmpeg"

DEEZER_API = "https://api.deezer.com"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

shazam = Shazam()


# =========================================================
# YORDAMCHI
# =========================================================

def format_time(seconds):
    if not seconds:
        return "0:00"

    minutes, seconds = divmod(int(seconds), 60)

    return f"{minutes}:{seconds:02d}"


def is_url(text):
    return bool(
        re.match(
            r"^https?://",
            text.strip(),
            re.IGNORECASE
        )
    )


def search_deezer(query, limit=10):
    """
    Deezer'dan qo'shiq/ijrochi bo'yicha qidiradi.
    Lyrics parchasi bo'yicha ham ba'zi hollarda
    tegishli natijalarni qaytarishi mumkin.
    """

    try:

        response = requests.get(
            f"{DEEZER_API}/search",
            params={
                "q": query,
                "limit": limit
            },
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        return data.get("data", [])

    except Exception:

        logger.exception(
            "DEEZER SEARCH XATOSI"
        )

        return []


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "👋 Assalomu aleykum\n\n"

        "• Instagram - post, stories, reels;\n"
        "• YouTube - video, shorts, audio;\n"
        "• Tik Tok - suv belgisiz video;\n"
        "• Facebook - reels;\n"
        "• Pinterest - rasm, video;\n"
        "• Snapchat - rasm, video;\n"
        "• Likee - rasm, video;\n"
        "• Threads - rasm, video;\n"
        "• OK.ru - video.\n\n"

        "🎵 Qo'shiq topish:\n"
        "• Qo'shiq nomini yozing;\n"
        "• Ijrochi nomini yozing;\n"
        "• Qo'shiq so'zidan parcha yozing;\n"
        "• Audio/video yuboring — qo'shiqni aniqlayman.\n\n"

        "🚀 Media yuklashni boshlash uchun "
        "uning havolasini yoki nomini yuboring."
    )


# =========================================================
# SHAZAM
# =========================================================

async def handle_media_music(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    status = await update.message.reply_text(
        "🔍 Musiqa aniqlanmoqda..."
    )

    job_id = str(uuid.uuid4())

    work_dir = os.path.join(
        DOWNLOAD_DIR,
        job_id
    )

    os.makedirs(
        work_dir,
        exist_ok=True
    )

    file_path = os.path.join(
        work_dir,
        "media_file"
    )

    try:

        message = update.message

        media = (
            message.voice
            or message.audio
            or message.video
            or message.video_note
        )

        telegram_file = await context.bot.get_file(
            media.file_id
        )

        await telegram_file.download_to_drive(
            file_path
        )

        result = await shazam.recognize(
            file_path
        )

        track = result.get("track")

        if not track:

            await status.edit_text(
                "❌ Ushbu fayldan qo'shiq topilmadi."
            )

            return

        title = track.get(
            "title",
            "Noma'lum"
        )

        artist = track.get(
            "subtitle",
            "Noma'lum"
        )

        # Aniqlangan qo'shiqni saqlab qo'yamiz
        search_text = f"{artist} {title}"

        await status.edit_text(

            "🎵 **Qo'shiq topildi!**\n\n"

            f"👤 **Ijrochi:** {artist}\n"
            f"🎧 **Nomi:** {title}\n\n"

            "🔎 Qo'shiqni yuklash uchun "
            "quyidagi tugmani bosing.",

            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📥 MP3 yuklash",
                        callback_data="shazam_download"
                    )
                ]
            ]),

            parse_mode="Markdown"
        )

        context.user_data[
            "shazam_result"
        ] = {
            "title": title,
            "artist": artist,
            "search_text": search_text
        }

    except Exception:

        logger.exception(
            "SHAZAM XATOSI"
        )

        await status.edit_text(
            "❌ Musiqani aniqlashda xatolik yuz berdi."
        )

    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )


# =========================================================
# URL YUKLASH
# =========================================================

async def download_url(
    update: Update,
    url: str
):

    status = await update.message.reply_text(
        "⏳ Media yuklanmoqda..."
    )

    job_id = str(uuid.uuid4())

    work_dir = os.path.join(
        DOWNLOAD_DIR,
        job_id
    )

    os.makedirs(
        work_dir,
        exist_ok=True
    )

    output = os.path.join(
        work_dir,
        "%(title)s.%(ext)s"
    )

    try:

        ydl_opts = {

            "format":
                "bestvideo[height<=720]+bestaudio/"
                "best[height<=720]/best",

            "outtmpl": output,

            "merge_output_format": "mp4",

            "noplaylist": True,

            "quiet": True,

            "no_warnings": True,

            "retries": 3,

            "fragment_retries": 3,

            "socket_timeout": 30,

            "ffmpeg_location": FFMPEG,

            "restrictfilenames": True,
        }

        logger.info(
            "URL yuklanmoqda: %s",
            url
        )

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            ydl.download([url])

        media_files = []

        for filename in os.listdir(
            work_dir
        ):

            if filename.lower().endswith(
                (
                    ".mp4",
                    ".mkv",
                    ".webm",
                    ".mov"
                )
            ):

                media_files.append(
                    os.path.join(
                        work_dir,
                        filename
                    )
                )

        if not media_files:

            await status.edit_text(
                "❌ Ushbu mediani yuklab bo'lmadi."
            )

            return

        media_file = max(
            media_files,
            key=os.path.getsize
        )

        size_mb = (
            os.path.getsize(media_file)
            / 1024
            / 1024
        )

        logger.info(
            "Video tayyor: %.2f MB",
            size_mb
        )

        # Agar 14 MB dan katta bo'lsa,
        # siqishga urinib ko'ramiz.
        if size_mb > TARGET_SIZE_MB:

            await status.edit_text(
                f"📦 Video {size_mb:.1f} MB.\n"
                "🔄 Hajmi kichraytirilmoqda..."
            )

            compressed = os.path.join(
                work_dir,
                "compressed.mp4"
            )

            compress_video(
                media_file,
                compressed
            )

            if os.path.exists(compressed):

                media_file = compressed

        final_size = (
            os.path.getsize(media_file)
            / 1024
            / 1024
        )

        with open(
            media_file,
            "rb"
        ) as video:

            await update.message.reply_video(
                video=video,
                caption="@yuklatgbot orqali yuklab olindi"
            )

        await status.delete()

        logger.info(
            "Telegramga yuborildi: %.2f MB",
            final_size
        )

    except Exception:

        logger.exception(
            "URL YUKLASH XATOSI"
        )

        await status.edit_text(
            "❌ Media yuklashda xatolik yuz berdi."
        )

    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )


# =========================================================
# VIDEO SIQISH
# =========================================================

def compress_video(
    input_file,
    output_file
):

    """
    14 MB atrofida video qilishga urinadi.
    """

    try:

        duration = get_duration(
            input_file
        )

        if not duration:
            duration = 60

        target_bytes = (
            TARGET_SIZE_MB
            * 1024
            * 1024
        )

        # Audio uchun 64 kbps ajratamiz
        audio_bitrate = 64000

        total_bitrate = (
            target_bytes * 8 / duration
        )

        video_bitrate = (
            int(total_bitrate)
            - audio_bitrate
        )

        video_bitrate = max(
            video_bitrate,
            80000
        )

        video_k = int(
            video_bitrate / 1000
        )

        cmd = [
            FFMPEG,
            "-y",
            "-i",
            input_file,

            "-vf",
            "scale='min(640,iw)':-2",

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-b:v",
            f"{video_k}k",

            "-maxrate",
            f"{video_k}k",

            "-bufsize",
            f"{video_k * 2}k",

            "-c:a",
            "aac",

            "-b:a",
            "64k",

            "-movflags",
            "+faststart",

            output_file
        ]

        subprocess_run(cmd)

    except Exception:

        logger.exception(
            "VIDEO SIQISH XATOSI"
        )


def get_duration(file_path):

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        file_path
    ]

    try:

        result = subprocess_run(
            cmd,
            capture_output=True
        )

        return float(
            result.stdout.strip()
        )

    except Exception:

        return 0


def subprocess_run(
    command,
    capture_output=False
):

    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE
        if capture_output
        else subprocess.DEVNULL,
        stderr=subprocess.PIPE
        if capture_output
        else subprocess.DEVNULL
    )


# =========================================================
# QO'SHIQ QIDIRISH
# =========================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text.strip()

    # -----------------------------------------------------
    # URL
    # -----------------------------------------------------

    if is_url(text):

        await download_url(
            update,
            text
        )

        return

    # -----------------------------------------------------
    # MUSIQA QIDIRUV
    # -----------------------------------------------------

    status = await update.message.reply_text(
        "🔎 Qo'shiq qidirilmoqda..."
    )

    try:

        # Avval Deezer
        tracks = search_deezer(
            text,
            limit=10
        )

        # -------------------------------------------------
        # DEEZER NATIJALARI
        # -------------------------------------------------

        if tracks:

            show_music_results(
                context,
                tracks,
                text,
                update.message.message_id
            )

            result_text = (
                f"🎵 **{text}** bo'yicha topilgan "
                "qo'shiqlar:\n\n"
            )

            buttons = []

            row = []

            for index, track in enumerate(
                tracks[:10],
                start=1
            ):

                title = track.get(
                    "title",
                    "Noma'lum"
                )

                artist_data = track.get(
                    "artist",
                    {}
                )

                artist = artist_data.get(
                    "name",
                    "Noma'lum"
                )

                duration = format_time(
                    track.get(
                        "duration",
                        0
                    )
                )

                result_text += (
                    f"{index}. "
                    f"{artist} — {title} "
                    f"({duration})\n"
                )

                song_id = (
                    f"{update.message.message_id}"
                    f"_{index}"
                )

                button = InlineKeyboardButton(
                    str(index),
                    callback_data=
                        f"music:{song_id}"
                )

                row.append(button)

                if len(row) == 5:

                    buttons.append(row)
                    row = []

            if row:
                buttons.append(row)

            buttons.append([
                InlineKeyboardButton(
                    "❌ Yopish",
                    callback_data="cancel_music"
                )
            ])

            await status.edit_text(
                result_text,
                reply_markup=
                    InlineKeyboardMarkup(buttons),
                parse_mode="Markdown"
            )

            return

        # -------------------------------------------------
        # DEEZER TOPMASA
        # -------------------------------------------------

        await status.edit_text(
            "🔎 Birinchi manbada topilmadi.\n"
            "🔄 Qo'shimcha qidiruv qilinmoqda..."
        )

        # YouTube zaxira qidiruvi
        search_results = search_youtube(
            text,
            limit=10
        )

        if not search_results:

            await status.edit_text(
                "❌ Qo'shiq topilmadi.\n\n"
                "Ijrochi va qo'shiq nomini "
                "birga yozib ko'ring yoki "
                "qo'shiqdan uzunroq parcha yuboring."
            )

            return

        result_text = (
            f"🎵 **{text}** bo'yicha natijalar:\n\n"
        )

        buttons = []

        row = []

        context.user_data.setdefault(
            "music_results",
            {}
        )

        for index, entry in enumerate(
            search_results[:10],
            start=1
        ):

            title = entry.get(
                "title",
                "Noma'lum"
            )

            duration = format_time(
                entry.get(
                    "duration",
                    0
                )
            )

            uploader = entry.get(
                "uploader",
                ""
            )

            result_text += (
                f"{index}. {title}"
                f"{' — ' + uploader if uploader else ''} "
                f"({duration})\n"
            )

            song_id = (
                f"{update.message.message_id}"
                f"_yt_{index}"
            )

            context.user_data[
                "music_results"
            ][song_id] = {

                "title": title,

                "artist": uploader,

                "search_text": title,

                "url": entry.get(
                    "webpage_url"
                )
            }

            row.append(
                InlineKeyboardButton(
                    str(index),
                    callback_data=
                        f"music:{song_id}"
                )
            )

            if len(row) == 5:

                buttons.append(row)
                row = []

        if row:
            buttons.append(row)

        buttons.append([
            InlineKeyboardButton(
                "❌ Yopish",
                callback_data="cancel_music"
            )
        ])

        await status.edit_text(
            result_text,
            reply_markup=
                InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )

    except Exception:

        logger.exception(
            "MUSIQA QIDIRUV XATOSI"
        )

        await status.edit_text(
            "❌ Qidiruvda xatolik yuz berdi."
        )


# =========================================================
# DEEZER NATIJALARINI SAQLASH
# =========================================================

def show_music_results(
    context,
    tracks,
    query,
    message_id
):

    context.user_data.setdefault(
        "music_results",
        {}
    )

    for index, track in enumerate(
        tracks[:10],
        start=1
    ):

        title = track.get(
            "title",
            "Noma'lum"
        )

        artist_data = track.get(
            "artist",
            {}
        )

        artist = artist_data.get(
            "name",
            "Noma'lum"
        )

        song_id = (
            f"{message_id}_{index}"
        )

        context.user_data[
            "music_results"
        ][song_id] = {

            "title": title,

            "artist": artist,

            "search_text":
                f"{artist} {title}",

            "deezer_url":
                track.get("link")
        }


# =========================================================
# YOUTUBE ZAXIRA QIDIRUV
# =========================================================

def search_youtube(
    query,
    limit=10
):

    try:

        ydl_opts = {

            "quiet": True,

            "no_warnings": True,

            "extract_flat": True,

            "skip_download": True,

            "ignoreerrors": True,

            "noplaylist": True,

            "default_search":
                f"ytsearch{limit}",
        }

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                query,
                download=False
            )

        if not info:

            return []

        return [
            item
            for item in info.get(
                "entries",
                []
            )
            if item
        ]

    except Exception:

        logger.exception(
            "YOUTUBE SEARCH XATOSI"
        )

        return []


# =========================================================
# AUDIO YUKLASH
# =========================================================

def download_audio(
    search_or_url,
    work_dir
):

    output = os.path.join(
        work_dir,
        "%(title)s.%(ext)s"
    )

    ydl_opts = {

        "format":
            "bestaudio[ext=m4a]/"
            "bestaudio/best",

        "outtmpl":
            output,

        "noplaylist":
            True,

        "quiet":
            True,

        "no_warnings":
            True,

        "retries":
            3,

        "fragment_retries":
            3,

        "socket_timeout":
            30,

        "ffmpeg_location":
            FFMPEG,

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

        "postprocessor_args": [
            "-ar",
            "44100",

            "-ac",
            "2"
        ],
    }

    with yt_dlp.YoutubeDL(
        ydl_opts
    ) as ydl:

        info = ydl.extract_info(
            search_or_url,
            download=True
        )

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

    if not mp3_files:

        return None, info

    audio_file = max(
        mp3_files,
        key=os.path.getsize
    )

    return audio_file, info


# =========================================================
# CALLBACK
# =========================================================

async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    # -----------------------------------------------------
    # YOPISH
    # -----------------------------------------------------

    if query.data == "cancel_music":

        try:
            await query.message.delete()
        except Exception:
            pass

        return

    # -----------------------------------------------------
    # SHAZAM NATIJASI
    # -----------------------------------------------------

    if query.data == "shazam_download":

        song = context.user_data.get(
            "shazam_result"
        )

        if not song:

            await query.message.reply_text(
                "❌ Natija eskirgan."
            )

            return

        await send_song(
            query,
            song
        )

        return

    # -----------------------------------------------------
    # QO'SHIQ NATIJASI
    # -----------------------------------------------------

    if not query.data.startswith(
        "music:"
    ):

        return

    song_id = query.data.split(
        "music:",
        1
    )[1]

    music_results = context.user_data.get(
        "music_results",
        {}
    )

    song = music_results.get(
        song_id
    )

    if not song:

        await query.message.reply_text(
            "❌ Qidiruv natijasi eskirgan.\n"
            "Qo'shiqni qaytadan qidiring."
        )

        return

    await send_song(
        query,
        song
    )


# =========================================================
# QO'SHIQNI YUBORISH
# =========================================================

async def send_song(
    query,
    song
):

    title = song.get(
        "title",
        "Qo'shiq"
    )

    artist = song.get(
        "artist",
        ""
    )

    search_text = song.get(
        "search_text"
    )

    url = song.get(
        "url"
    )

    status = await query.message.reply_text(
        f"📥 {artist} — {title}\n\n"
        "⏳ Audio yuklanmoqda..."
    )

    job_id = str(uuid.uuid4())

    work_dir = os.path.join(
        DOWNLOAD_DIR,
        job_id
    )

    os.makedirs(
        work_dir,
        exist_ok=True
    )

    try:

        # Agar aniq URL bo'lsa,
        # shu URL dan yuklaymiz.
        if url:

            source = url

        else:

            source = (
                f"ytsearch1:{search_text}"
            )

        logger.info(
            "Audio source: %s",
            source
        )

        audio_file, info = download_audio(
            source,
            work_dir
        )

        if not audio_file:

            await status.edit_text(
                "❌ Audio fayl tayyorlanmadi."
            )

            return

        size_mb = (
            os.path.getsize(audio_file)
            / 1024
            / 1024
        )

        logger.info(
            "MP3 tayyor: %.2f MB",
            size_mb
        )

        with open(
            audio_file,
            "rb"
        ) as audio:

            await query.message.reply_audio(

                audio=audio,

                title=title,

                performer=artist,

                caption=
                    "@yuklatgbot orqali yuklab olindi"
            )

        await status.delete()

    except yt_dlp.utils.DownloadError:

        logger.exception(
            "AUDIO YUKLASH XATOSI"
        )

        await status.edit_text(
            "❌ Ushbu qo'shiqni yuklab bo'lmadi.\n\n"
            "🔄 Boshqa natijani tanlab ko'ring."
        )

    except Exception:

        logger.exception(
            "AUDIO SEND XATOSI"
        )

        await status.edit_text(
            "❌ Musiqani yuklashda xatolik yuz berdi."
        )

    finally:

        shutil.rmtree(
            work_dir,
            ignore_errors=True
        )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN topilmadi!"
        )

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    # /start
    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # Audio / Voice / Video -> Shazam
    app.add_handler(
        MessageHandler(
            filters.VOICE
            | filters.AUDIO
            | filters.VIDEO
            | filters.VIDEO_NOTE,
            handle_media_music
        )
    )

    # Text -> URL yoki musiqa qidirish
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_text
        )
    )

    # Tugmalar
    app.add_handler(
        CallbackQueryHandler(
            handle_callback
        )
    )

    logger.info(
        "================================"
    )

    logger.info(
        "BOT ISHLAYAPTI"
    )

    logger.info(
        "================================"
    )

    app.run_polling()


if __name__ == "__main__":
    main()
