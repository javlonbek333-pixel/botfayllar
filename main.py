import asyncio
import base64
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import requests
import yt_dlp

from shazamio import Shazam

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)

from telegram.constants import ChatAction

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# =========================================================
# SOZLAMALAR
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

VIDEO_KBPS = 310
AUDIO_KBPS = 64
FPS = 30

MAX_UPLOAD_MB = 100
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

SEARCH_LIMIT = 100
PAGE_SIZE = 10


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# PAPKA
# =========================================================

BASE_DIR = Path(tempfile.gettempdir()) / "telegram_downloader"

BASE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# =========================================================
# URL ANIQLASH
# =========================================================

URL_RE = re.compile(
    r"https?://[^\s]+",
    re.IGNORECASE,
)


def extract_url(text):
    if not text:
        return None

    match = URL_RE.search(text)

    if not match:
        return None

    return match.group(0).rstrip(
        ".,!?)]}>\"'"
    )


# =========================================================
# QO‘LLAB-QUVVATLANADIGAN SAYTLAR
# =========================================================

SUPPORTED_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "instagr.am",
    "tiktok.com",
    "vm.tiktok.com",
    "facebook.com",
    "fb.watch",
    "ok.ru",
    "odnoklassniki.ru",
)


def is_supported_url(url):
    if not url:
        return False

    url_lower = url.lower()

    return any(
        domain in url_lower
        for domain in SUPPORTED_DOMAINS
    )


def is_youtube_url(url):
    if not url:
        return False

    u = url.lower()

    return (
        "youtube.com" in u
        or "youtu.be" in u
    )


# =========================================================
# FFMPEG
# =========================================================

def find_ffmpeg():
    path = shutil.which("ffmpeg")

    if path:
        return path

    possible = [
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]

    for p in possible:
        if Path(p).exists():
            return p

    return "ffmpeg"


FFMPEG = find_ffmpeg()


# =========================================================
# YOUTUBE COOKIE
# =========================================================

def prepare_cookie_file(folder):
    """
    Railway Variables:
        YOUTUBE_COOKIES_B64

    Noto‘g‘ri cookie bo‘lsa, YouTube yuklashni
    cookie-siz davom ettirishga imkon beradi.
    """

    b64 = os.getenv(
        "YOUTUBE_COOKIES_B64",
        "",
    ).strip()

    if not b64:
        return None

    try:
        raw = base64.b64decode(
            b64,
            validate=True,
        )

        # UTF-8
        try:
            text = raw.decode("utf-8")

        except UnicodeDecodeError:

            # UTF-16
            try:
                text = raw.decode("utf-16")

            except UnicodeDecodeError:

                # UTF-16 LE
                try:
                    text = raw.decode("utf-16-le")

                except UnicodeDecodeError:

                    # UTF-16 BE
                    try:
                        text = raw.decode("utf-16-be")

                    except UnicodeDecodeError:
                        logger.warning(
                            "YouTube cookie UTF format noto‘g‘ri."
                        )
                        return None

        text = text.lstrip("\ufeff")

        lines = text.splitlines()

        if not lines:
            return None

        first_line = lines[0].strip()

        if not (
            first_line.startswith(
                "# Netscape HTTP Cookie File"
            )
            or first_line.startswith(
                "# HTTP Cookie File"
            )
        ):
            logger.warning(
                "YOUTUBE_COOKIES_B64 Netscape cookies.txt formatida emas."
            )
            return None

        cookie_path = (
            Path(folder)
            / "youtube_cookies.txt"
        )

        cookie_path.write_text(
            text,
            encoding="utf-8",
            newline="\n",
        )

        return str(cookie_path)

    except Exception:
        logger.exception(
            "Cookie tayyorlashda xatolik"
        )

        return None


# =========================================================
# YT-DLP SOZLAMALARI
# =========================================================

def yt_opts(folder, use_cookie=True):

    opts = {
        "outtmpl": str(
            Path(folder) / "%(id)s.%(ext)s"
        ),

        "noplaylist": True,

        "quiet": True,

        "no_warnings": True,

        "retries": 3,

        "fragment_retries": 3,

        "concurrent_fragment_downloads": 4,

        "merge_output_format": "mp4",

        "format": (
            "bv*+ba/"
            "best"
        ),

        "socket_timeout": 30,
    }

    if use_cookie:
        cookie_file = prepare_cookie_file(
            folder
        )

        if cookie_file:
            opts["cookiefile"] = cookie_file

    return opts


# =========================================================
# VIDEO SCALE
# =========================================================

def get_scale_filter(width, height):

    if height > width:

        return (
            "scale="
            "w='min(480,iw)':"
            "h='min(854,ih)':"
            "force_original_aspect_ratio=decrease:"
            "force_divisible_by=2"
        )

    if width > height:

        return (
            "scale="
            "w='min(854,iw)':"
            "h='min(480,ih)':"
            "force_original_aspect_ratio=decrease:"
            "force_divisible_by=2"
        )

    return (
        "scale="
        "w='min(480,iw)':"
        "h='min(480,ih)':"
        "force_original_aspect_ratio=decrease:"
        "force_divisible_by=2"
    )


# =========================================================
# VIDEO O‘LCHAMI
# =========================================================

def get_video_size(path):

    cmd = [
        FFMPEG,
        "-i",
        str(path),
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="ignore",
    )

    text = result.stderr

    match = re.search(
        r"Video:.*?(\d{2,5})x(\d{2,5})",
        text,
    )

    if match:

        return (
            int(match.group(1)),
            int(match.group(2)),
        )

    return 854, 480


# =========================================================
# VIDEO COMPRESS
# =========================================================

def compress_video(input_path, output_path):

    width, height = get_video_size(
        input_path
    )

    scale_filter = get_scale_filter(
        width,
        height,
    )

    cmd = [
        FFMPEG,

        "-y",

        "-i",
        str(input_path),

        "-vf",
        scale_filter,

        "-r",
        str(FPS),

        "-c:v",
        "libx264",

        "-preset",
        "ultrafast",

        "-b:v",
        f"{VIDEO_KBPS}k",

        "-maxrate",
        f"{VIDEO_KBPS}k",

        "-bufsize",
        f"{VIDEO_KBPS * 2}k",

        "-pix_fmt",
        "yuv420p",

        "-threads",
        "4",

        "-map_metadata",
        "-1",

        "-c:a",
        "aac",

        "-b:a",
        f"{AUDIO_KBPS}k",

        "-ac",
        "2",

        "-ar",
        "44100",

        "-movflags",
        "+faststart",

        str(output_path),
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="ignore",
    )

    if result.returncode != 0:

        logger.error(
            "FFmpeg error:\n%s",
            result.stderr[-5000:],
        )

        raise RuntimeError(
            "FFmpeg xatosi"
        )

    return output_path


# =========================================================
# AUDIO EXTRACT
# =========================================================

def extract_audio(
    input_path,
    output_path,
):

    cmd = [
        FFMPEG,

        "-y",

        "-i",
        str(input_path),

        "-vn",

        "-c:a",
        "libmp3lame",

        "-b:a",
        "128k",

        "-ar",
        "44100",

        "-ac",
        "2",

        str(output_path),
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="ignore",
    )

    if result.returncode != 0:

        logger.error(
            "Audio FFmpeg error:\n%s",
            result.stderr[-5000:],
        )

        raise RuntimeError(
            "Audio chiqarishda xatolik"
        )

    return output_path


# =========================================================
# YOUTUBE SONG SEARCH
# =========================================================

def youtube_song_search(
    query,
    limit=100,
):

    opts = {
        "quiet": True,

        "no_warnings": True,

        "noplaylist": True,

        "extract_flat": True,

        "skip_download": True,

        "socket_timeout": 20,
    }

    search_url = (
        f"ytsearch{limit}:{query}"
    )

    with yt_dlp.YoutubeDL(opts) as ydl:

        data = ydl.extract_info(
            search_url,
            download=False,
        )

    results = []

    for entry in data.get(
        "entries",
        [],
    ):

        if not entry:
            continue

        video_id = entry.get("id")

        if not video_id:
            continue

        title = entry.get(
            "title",
            "Noma'lum",
        )

        url = (
            entry.get("webpage_url")
            or f"https://www.youtube.com/watch?v={video_id}"
        )

        results.append(
            {
                "id": video_id,
                "title": title,
                "url": url,
            }
        )

        if len(results) >= limit:
            break

    return results


# =========================================================
# SONG PAGINATION
# =========================================================

def song_keyboard(
    search_id,
    results,
    page,
):

    start = page * PAGE_SIZE

    end = min(
        start + PAGE_SIZE,
        len(results),
    )

    buttons = []

    for index in range(
        start,
        end,
    ):

        song = results[index]

        title = song.get(
            "title",
            "Noma'lum",
        )

        if len(title) > 48:
            title = (
                title[:45]
                + "..."
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"🎵 {index + 1}. {title}",
                    callback_data=(
                        f"searchsong|"
                        f"{search_id}|"
                        f"{index}"
                    ),
                )
            ]
        )

    navigation = []

    if page > 0:

        navigation.append(
            InlineKeyboardButton(
                "◀️ Oldingi",
                callback_data=(
                    f"songpage|"
                    f"{search_id}|"
                    f"{page - 1}"
                ),
            )
        )

    if end < len(results):

        navigation.append(
            InlineKeyboardButton(
                "Keyingi ▶️",
                callback_data=(
                    f"songpage|"
                    f"{search_id}|"
                    f"{page + 1}"
                ),
            )
        )

    if navigation:
        buttons.append(navigation)

    total_pages = (
        (len(results) + PAGE_SIZE - 1)
        // PAGE_SIZE
    )

    buttons.append(
        [
            InlineKeyboardButton(
                f"📄 {page + 1}/{total_pages}",
                callback_data="noop",
            )
        ]
    )

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

    text = (
        "👋 Salom!\n\n"
        "Media yuklashni boshlash uchun "
        "uning havolasini yuboring.\n\n"
        "🎵 Qo‘shiq qidirish uchun "
        "qo‘shiqchi yoki qo‘shiq nomini yozing."
    )

    await update.message.reply_text(
        text
    )


# =========================================================
# URL MESSAGE
# =========================================================

async def url_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    text = (
        update.message.text or ""
    ).strip()

    url = extract_url(text)

    if url:

        if not is_supported_url(url):

            await update.message.reply_text(
                "❌ Bu havola qo‘llab-quvvatlanmaydi."
            )

            return

        if is_youtube_url(url):

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🎬 Video",
                            callback_data=(
                                f"ytvideo|{url}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🎵 MP3",
                            callback_data=(
                                f"ytmp3|{url}"
                            ),
                        )
                    ],
                ]
            )

            await update.message.reply_text(
                "Yuklash turini tanlang:",
                reply_markup=keyboard,
            )

            return

        await process_url(
            update,
            context,
            url,
        )

        return

    # URL bo‘lmasa qo‘shiq qidirish
    await search_song_text(
        update,
        context,
        text,
    )


# =========================================================
# SONG TEXT SEARCH
# =========================================================

async def search_song_text(
    update,
    context,
    query,
):

    if not query:

        await update.message.reply_text(
            "Qo‘shiqchi yoki qo‘shiq nomini yozing."
        )

        return

    message = await update.message.reply_text(
        "⏳ Yuklanmoqda..."
    )

    try:

        results = await asyncio.to_thread(
            youtube_song_search,
            query,
            100,
        )

        if not results:

            await message.edit_text(
                "❌ Qo‘shiq topilmadi."
            )

            return

        search_id = uuid.uuid4().hex[:8]

        context.bot_data.setdefault(
            "song_searches",
            {},
        )[search_id] = results

        keyboard = song_keyboard(
            search_id,
            results,
            0,
        )

        await message.edit_text(
            f"🎵 {len(results)} ta natija topildi.\n\n"
            f"10 tadan ko‘rsatilyapti:",
            reply_markup=keyboard,
        )

    except Exception as e:

        logger.exception(
            "Qo‘shiq qidirish xatosi"
        )

        await message.edit_text(
            "❌ Qo'shiq qidirishda xatolik:\n"
            f"{e}"
        )


# =========================================================
# SONG PAGE CALLBACK
# =========================================================

async def song_page_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    parts = (
        query.data or ""
    ).split("|")

    if len(parts) != 3:
        return

    search_id = parts[1]

    try:
        page = int(parts[2])
    except ValueError:
        return

    searches = context.bot_data.get(
        "song_searches",
        {},
    )

    results = searches.get(
        search_id
    )

    if not results:

        await query.answer(
            "Natijalar eskirgan.",
            show_alert=True,
        )

        return

    keyboard = song_keyboard(
        search_id,
        results,
        page,
    )

    await query.edit_message_reply_markup(
        reply_markup=keyboard
    )


# =========================================================
# SEARCH SONG BUTTON
# =========================================================

async def search_song_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    parts = (
        query.data or ""
    ).split("|")

    if len(parts) != 3:
        return

    search_id = parts[1]

    try:
        index = int(parts[2])
    except ValueError:
        return

    searches = context.bot_data.get(
        "song_searches",
        {},
    )

    results = searches.get(
        search_id
    )

    if not results:
        await query.answer(
            "Natijalar eskirgan.",
            show_alert=True,
        )
        return

    if index < 0 or index >= len(results):
        return

    selected = results[index]

    title = selected.get(
        "title",
        "Qo‘shiq",
    )

    await download_song(
        query,
        selected["url"],
        title,
    )


# =========================================================
# DOWNLOAD SONG
# =========================================================

async def download_song(
    query,
    url,
    title,
):

    work = (
        BASE_DIR
        / uuid.uuid4().hex
    )

    work.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        await query.edit_message_text(
            "⏳ Yuklanmoqda..."
        )

        opts = yt_opts(
            work,
            use_cookie=False,
        )

        opts["format"] = (
            "bestaudio/best"
        )

        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ]

        with yt_dlp.YoutubeDL(
            opts
        ) as ydl:

            info = await asyncio.to_thread(
                ydl.extract_info,
                url,
                download=True,
            )

            filename = Path(
                ydl.prepare_filename(
                    info
                )
            )

        mp3 = filename.with_suffix(
            ".mp3"
        )

        if not mp3.exists():

            candidates = list(
                work.glob("*.mp3")
            )

            if candidates:
                mp3 = candidates[0]

        if not mp3.exists():

            raise RuntimeError(
                "MP3 fayl topilmadi."
            )

        if (
            mp3.stat().st_size
            > MAX_UPLOAD_BYTES
        ):

            raise RuntimeError(
                "Fayl 100 MB dan katta."
            )

        await query.message.reply_audio(
            audio=InputFile(
                str(mp3)
            ),
            title=title[:64],
        )

        try:
            await query.message.delete()
        except Exception:
            pass

    except Exception as e:

        logger.exception(
            "Song download error"
        )

        try:
            await query.edit_message_text(
                "❌ Qo‘shiqni yuklashda xatolik:\n"
                f"{e}"
            )
        except Exception:
            pass

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True,
        )


# =========================================================
# YOUTUBE VIDEO / MP3 CALLBACK
# =========================================================

async def media_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    data = query.data or ""

    if data.startswith(
        "ytvideo|"
    ):

        url = data.split(
            "|",
            1,
        )[1]

        await youtube_download(
            query,
            url,
            audio=False,
        )

        return

    if data.startswith(
        "ytmp3|"
    ):

        url = data.split(
            "|",
            1,
        )[1]

        await youtube_download(
            query,
            url,
            audio=True,
        )

        return


# =========================================================
# YOUTUBE DOWNLOAD
# =========================================================

async def youtube_download(
    query,
    url,
    audio=False,
):

    work = (
        BASE_DIR
        / uuid.uuid4().hex
    )

    work.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        await query.edit_message_text(
            "⏳ Yuklanmoqda..."
        )

        opts = yt_opts(
            work,
            use_cookie=True,
        )

        if audio:

            opts["format"] = (
                "bestaudio/best"
            )

            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "128",
                }
            ]

        else:

            opts["format"] = (
                "bv*+ba/"
                "best"
            )

        with yt_dlp.YoutubeDL(
            opts
        ) as ydl:

            info = await asyncio.to_thread(
                ydl.extract_info,
                url,
                download=True,
            )

            filename = Path(
                ydl.prepare_filename(
                    info
                )
            )

        if audio:

            output = filename.with_suffix(
                ".mp3"
            )

            if not output.exists():

                candidates = list(
                    work.glob("*.mp3")
                )

                if candidates:
                    output = candidates[0]

            if not output.exists():
                raise RuntimeError(
                    "MP3 topilmadi."
                )

            await query.message.reply_audio(
                audio=InputFile(
                    str(output)
                ),
                title=(
                    info.get(
                        "title",
                        "Qo‘shiq",
                    )[:64]
                ),
            )

        else:

            source = filename

            if not source.exists():

                candidates = list(
                    work.glob("*")
                )

                video_candidates = [
                    x
                    for x in candidates
                    if x.suffix.lower()
                    in (
                        ".mp4",
                        ".mkv",
                        ".webm",
                        ".mov",
                    )
                ]

                if video_candidates:
                    source = video_candidates[0]

            if not source.exists():
                raise RuntimeError(
                    "Video topilmadi."
                )

            output = (
                work
                / "final.mp4"
            )

            await asyncio.to_thread(
                compress_video,
                source,
                output,
            )

            if (
                output.stat().st_size
                > MAX_UPLOAD_BYTES
            ):
                raise RuntimeError(
                    "Video 100 MB dan katta bo‘lib qoldi."
                )

            await query.message.reply_video(
                video=InputFile(
                    str(output)
                ),
                supports_streaming=True,
                width=None,
                height=None,
            )

        try:
            await query.message.delete()
        except Exception:
            pass

    except Exception as e:

        logger.exception(
            "YouTube download error"
        )

        try:

            await query.edit_message_text(
                "❌ Videoni yuklashda xatolik:\n"
                f"{e}"
            )

        except Exception:
            pass

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True,
        )


# =========================================================
# GENERAL URL DOWNLOAD
# =========================================================

async def process_url(
    update,
    context,
    url,
):

    message = await update.message.reply_text(
        "⏳ Yuklanmoqda..."
    )

    work = (
        BASE_DIR
        / uuid.uuid4().hex
    )

    work.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        opts = yt_opts(
            work,
            use_cookie=False,
        )

        opts["format"] = (
            "bv*+ba/"
            "best"
        )

        with yt_dlp.YoutubeDL(
            opts
        ) as ydl:

            info = await asyncio.to_thread(
                ydl.extract_info,
                url,
                download=True,
            )

            filename = Path(
                ydl.prepare_filename(
                    info
                )
            )

        if not filename.exists():

            candidates = list(
                work.glob("*")
            )

            videos = [
                x
                for x in candidates
                if x.suffix.lower()
                in (
                    ".mp4",
                    ".mkv",
                    ".webm",
                    ".mov",
                )
            ]

            if videos:
                filename = videos[0]

        if not filename.exists():

            raise RuntimeError(
                "Video topilmadi."
            )

        # ---------------------------------------------
        # VIDEO
        # ---------------------------------------------

        output = (
            work
            / "final.mp4"
        )

        await asyncio.to_thread(
            compress_video,
            filename,
            output,
        )

        if (
            output.stat().st_size
            > MAX_UPLOAD_BYTES
        ):

            raise RuntimeError(
                "Video 100 MB dan katta."
            )

        await update.message.reply_video(
            video=InputFile(
                str(output)
            ),
            supports_streaming=True,
        )

        # ---------------------------------------------
        # SHAZAM
        # ---------------------------------------------

        await recognize_file_for_job(
            update,
            output,
            info,
        )

        try:
            await message.delete()
        except Exception:
            pass

    except Exception as e:

        logger.exception(
            "URL download error"
        )

        try:
            await message.edit_text(
                "❌ Videoni yuklashda xatolik:\n"
                f"{e}"
            )
        except Exception:
            pass

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True,
        )


# =========================================================
# SHAZAM
# =========================================================

async def recognize_file_for_job(
    update,
    video_path,
    info=None,
):

    work = (
        BASE_DIR
        / uuid.uuid4().hex
    )

    work.mkdir(
        parents=True,
        exist_ok=True,
    )

    audio = (
        work
        / "sample.mp3"
    )

    try:

        cmd = [
            FFMPEG,

            "-y",

            "-i",
            str(video_path),

            "-t",
            "90",

            "-vn",

            "-ac",
            "1",

            "-ar",
            "44100",

            "-c:a",
            "mp3",

            "-b:a",
            "128k",

            str(audio),
        ]

        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if result.returncode != 0:
            return

        shazam = Shazam()

        result = await shazam.recognize_song(
            str(audio)
        )

        track = (
            result.get("track")
            if result
            else None
        )

        if not track:
            return

        title = track.get(
            "title",
            "",
        )

        artist = track.get(
            "subtitle",
            "",
        )

        if not title:
            return

        job_id = uuid.uuid4().hex[:8]

        context = update.get_bot()._bot

        # Faqat callback uchun oddiy vaqtinchalik ma'lumot
        update.get_bot_data = None

    except Exception:
        logger.exception(
            "Shazam xatosi"
        )

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True,
        )


# =========================================================
# AUDIO / VIDEO RECOGNITION
# =========================================================

async def recognize_audio(
    update,
    context,
):

    message = update.message

    if message.voice:

        tg_file = await message.voice.get_file()

        suffix = ".ogg"

        file_size = (
            message.voice.file_size
            or 0
        )

    elif message.audio:

        tg_file = await message.audio.get_file()

        suffix = ".mp3"

        file_size = (
            message.audio.file_size
            or 0
        )

    elif message.video:

        tg_file = await message.video.get_file()

        suffix = ".mp4"

        file_size = (
            message.video.file_size
            or 0
        )

    elif message.video_note:

        tg_file = await message.video_note.get_file()

        suffix = ".mp4"

        file_size = (
            message.video_note.file_size
            or 0
        )

    else:
        return

    if file_size > 100 * 1024 * 1024:

        await message.reply_text(
            "❌ Fayl juda katta."
        )

        return

    status = await message.reply_text(
        "⏳ Yuklanmoqda..."
    )

    work = (
        BASE_DIR
        / uuid.uuid4().hex
    )

    work.mkdir(
        parents=True,
        exist_ok=True,
    )

    source = (
        work
        / f"source{suffix}"
    )

    sample = (
        work
        / "sample.mp3"
    )

    try:

        await tg_file.download_to_drive(
            custom_path=str(source)
        )

        cmd = [
            FFMPEG,

            "-y",

            "-i",
            str(source),

            "-t",
            "90",

            "-vn",

            "-ac",
            "1",

            "-ar",
            "44100",

            "-c:a",
            "mp3",

            "-b:a",
            "128k",

            str(sample),
        ]

        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "Audio tayyorlashda xatolik."
            )

        shazam = Shazam()

        recognized = await shazam.recognize_song(
            str(sample)
        )

        track = (
            recognized.get("track")
            if recognized
            else None
        )

        if not track:

            await status.edit_text(
                "❌ Qo‘shiq aniqlanmadi."
            )

            return

        title = track.get(
            "title",
            "",
        )

        artist = track.get(
            "subtitle",
            "",
        )

        if not title:

            await status.edit_text(
                "❌ Qo‘shiq aniqlanmadi."
            )

            return

        # YouTube orqali to‘liq qo‘shiq qidirish
        search_text = (
            f"{artist} {title}"
        ).strip()

        job_id = uuid.uuid4().hex[:8]

        context.bot_data.setdefault(
            "media_jobs",
            {},
        )[job_id] = {
            "artist": artist,
            "song": title,
            "search": search_text,
        }

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🎶 To‘liq qo‘shiq",
                        callback_data=(
                            f"fullsong|{job_id}"
                        ),
                    )
                ]
            ]
        )

        await status.edit_text(
            f"🎵 {artist} — {title}",
            reply_markup=keyboard,
        )

    except Exception as e:

        logger.exception(
            "Shazam recognize error"
        )

        try:
            await status.edit_text(
                "❌ Qo‘shiq aniqlashda xatolik:\n"
                f"{e}"
            )
        except Exception:
            pass

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True,
        )


# =========================================================
# FULL SONG CALLBACK
# =========================================================

async def full_song_callback(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    job_id = (
        query.data or ""
    ).split(
        "|",
        1,
    )[-1]

    jobs = context.bot_data.get(
        "media_jobs",
        {},
    )

    job = jobs.get(
        job_id
    )

    if not job:

        await query.answer(
            "Ma'lumot eskirgan.",
            show_alert=True,
        )

        return

    await query.edit_message_text(
        "⏳ Yuklanmoqda..."
    )

    work = (
        BASE_DIR
        / uuid.uuid4().hex
    )

    work.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        results = await asyncio.to_thread(
            youtube_song_search,
            job["search"],
            5,
        )

        if not results:

            raise RuntimeError(
                "To‘liq qo‘shiq topilmadi."
            )

        selected = results[0]

        opts = yt_opts(
            work,
            use_cookie=False,
        )

        opts["format"] = (
            "bestaudio/best"
        )

        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ]

        with yt_dlp.YoutubeDL(
            opts
        ) as ydl:

            info = await asyncio.to_thread(
                ydl.extract_info,
                selected["url"],
                download=True,
            )

            filename = Path(
                ydl.prepare_filename(
                    info
                )
            )

        mp3 = filename.with_suffix(
            ".mp3"
        )

        if not mp3.exists():

            candidates = list(
                work.glob("*.mp3")
            )

            if candidates:
                mp3 = candidates[0]

        if not mp3.exists():

            raise RuntimeError(
                "MP3 fayl topilmadi."
            )

        if (
            mp3.stat().st_size
            > MAX_UPLOAD_BYTES
        ):

            raise RuntimeError(
                "MP3 100 MB dan katta."
            )

        await query.message.reply_audio(
            audio=InputFile(
                str(mp3)
            ),
            title=(
                info.get(
                    "title",
                    job["song"],
                )[:64]
            ),
            performer=(
                job.get(
                    "artist",
                    "",
                )[:64]
            ),
        )

        try:
            await query.message.delete()
        except Exception:
            pass

    except Exception as e:

        logger.exception(
            "Full song error"
        )

        try:

            await query.edit_message_text(
                "❌ Qo‘shiqni yuklashda xatolik:\n"
                f"{e}"
            )

        except Exception:
            pass

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True,
        )


# =========================================================
# NOOP
# =========================================================

async def noop_callback(
    update,
    context,
):

    await update.callback_query.answer()


# =========================================================
# ERROR
# =========================================================

async def error_handler(
    update,
    context,
):

    logger.exception(
        "Telegram bot xatosi",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN topilmadi. "
            "Railway Variables ichiga BOT_TOKEN qo‘ying."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # START
    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    # YouTube VIDEO / MP3
    app.add_handler(
        CallbackQueryHandler(
            media_callback,
            pattern=r"^(ytvideo|ytmp3)\|",
        )
    )

    # 100 ta qo‘shiq sahifalari
    app.add_handler(
        CallbackQueryHandler(
            song_page_callback,
            pattern=r"^songpage\|",
        )
    )

    # Qo‘shiq tanlash
    app.add_handler(
        CallbackQueryHandler(
            search_song_callback,
            pattern=r"^searchsong\|",
        )
    )

    # To‘liq qo‘shiq
    app.add_handler(
        CallbackQueryHandler(
            full_song_callback,
            pattern=r"^fullsong\|",
        )
    )

    # No-op
    app.add_handler(
        CallbackQueryHandler(
            noop_callback,
            pattern=r"^noop$",
        )
    )

    # Voice / Audio / Video / Round video
    app.add_handler(
        MessageHandler(
            filters.VOICE
            | filters.AUDIO
            | filters.VIDEO
            | filters.VIDEO_NOTE,
            recognize_audio,
        )
    )

    # Text / URL / song search
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            url_message,
        )
    )

    app.add_error_handler(
        error_handler
    )

    logger.info(
        "Telegram bot ishga tushdi"
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
