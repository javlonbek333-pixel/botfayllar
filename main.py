import asyncio
import base64
import logging
import os
import re
import shutil
import subprocess
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

DOWNLOAD_DIR = Path(
    os.getenv("DOWNLOAD_DIR", "/tmp/videola")
)

DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# Video sifati
VIDEO_KBPS = 500

# Audio sifati
AUDIO_KBPS = 64

# Kod ichidagi maksimal video hajmi
MAX_UPLOAD_BYTES = 100_000_000  # 100 MB

# Bot caption
CAPTION = "@yuklatgbot orqali yuklab olindi"


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("videola_bot")


# =========================================================
# COMMAND
# =========================================================

def run_cmd(cmd, timeout=600):
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


# =========================================================
# FFPROBE
# =========================================================

def ffprobe_duration(path):
    r = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        60,
    )

    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def ffprobe_dimensions(path):
    r = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(path),
        ],
        60,
    )

    m = re.match(
        r"(\d+)x(\d+)",
        r.stdout.strip(),
    )

    if m:
        return int(m.group(1)), int(m.group(2))

    return None, None


def has_audio(path):
    r = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        60,
    )

    return bool(r.stdout.strip())


# =========================================================
# FILE
# =========================================================

def find_downloaded_file(folder):
    files = [
        p
        for p in folder.rglob("*")
        if p.is_file()
        and p.suffix.lower()
        not in {
            ".part",
            ".ytdl",
            ".json",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
        }
    ]

    return (
        max(
            files,
            key=lambda p: p.stat().st_size,
        )
        if files
        else None
    )


def safe_name(value, limit=70):
    value = re.sub(
        r"[^\w\-. ]+",
        "",
        value,
        flags=re.UNICODE,
    ).strip()

    return (value or "video")[:limit]


# =========================================================
# URL
# =========================================================

def is_url(text):
    return bool(
        re.match(
            r"^https?://",
            text.strip(),
            re.I,
        )
    )


def is_supported_url(url):
    hosts = (
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

    low = url.lower()

    return any(
        h in low
        for h in hosts
    )


# =========================================================
# YT-DLP
# =========================================================

def yt_opts(folder):
    opts = {
        "outtmpl": str(
            folder / "%(id)s.%(ext)s"
        ),

        "noplaylist": True,

        "quiet": True,

        "no_warnings": True,

        "retries": 3,

        "fragment_retries": 3,

        "concurrent_fragment_downloads": 4,

        "merge_output_format": "mp4",

        "format": "bv*+ba/b",
    }

    # YouTube cookies
    b64 = os.getenv(
        "YOUTUBE_COOKIES_B64",
        "",
    ).strip()

    cookie_file = os.getenv(
        "YOUTUBE_COOKIE_FILE",
        "",
    ).strip()

    if b64:
        try:
            p = folder / "youtube_cookies.txt"

            p.write_bytes(
                base64.b64decode(b64)
            )

            opts["cookiefile"] = str(p)

        except Exception:
            logger.exception(
                "Cookie decode xatosi"
            )

    elif (
        cookie_file
        and Path(cookie_file).exists()
    ):
        opts["cookiefile"] = cookie_file

    return opts


def download_url(url, folder):
    with yt_dlp.YoutubeDL(
        yt_opts(folder)
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=True,
        )

        title = (
            info.get("title")
            or "video"
        )

        p = Path(
            ydl.prepare_filename(info)
        )

        if not p.exists():
            p = find_downloaded_file(
                folder
            )

        return p, title


# =========================================================
# SCALE
# =========================================================

def get_scale_filter(width, height):
    """
    Vertikal:
        480x854 gacha

    Gorizontal:
        854x480 gacha

    Kvadrat:
        480x480 gacha
    """

    # Vertikal
    if height > width:
        return (
            "scale="
            "w='min(480,iw)':"
            "h='min(854,ih)':"
            "force_original_aspect_ratio=decrease:"
            "force_divisible_by=2"
        )

    # Gorizontal
    if width > height:
        return (
            "scale="
            "w='min(854,iw)':"
            "h='min(480,ih)':"
            "force_original_aspect_ratio=decrease:"
            "force_divisible_by=2"
        )

    # Kvadrat
    return (
        "scale="
        "w='min(480,iw)':"
        "h='min(480,ih)':"
        "force_original_aspect_ratio=decrease:"
        "force_divisible_by=2"
    )


# =========================================================
# VIDEO COMPRESS
# =========================================================

def compress_video(source, output):

    duration = ffprobe_duration(
        source
    )

    if duration <= 0:
        raise RuntimeError(
            "Video davomiyligi aniqlanmadi."
        )

    width, height = ffprobe_dimensions(
        source
    )

    if not width or not height:
        raise RuntimeError(
            "Video o'lchami aniqlanmadi."
        )

    audio_exists = has_audio(
        source
    )

    scale_filter = get_scale_filter(
        width,
        height,
    )

    # =====================================================
    # 500 kbps VIDEO
    # 64 kbps AUDIO
    # 30 FPS
    # =====================================================

    cmd = [
        "ffmpeg",

        "-y",

        "-i",
        str(source),

        "-map",
        "0:v:0",

        "-vf",
        scale_filter,

        "-r",
        "30",

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

        # Railway serverda resursni nazorat qilish
        "-threads",
        "4",

        "-map_metadata",
        "-1",

        "-movflags",
        "+faststart",
    ]

    if audio_exists:

        cmd += [
            "-map",
            "0:a:0",

            "-c:a",
            "aac",

            "-b:a",
            f"{AUDIO_KBPS}k",

            "-ac",
            "2",

            "-ar",
            "44100",
        ]

    else:
        cmd += [
            "-an"
        ]

    cmd += [
        "-f",
        "mp4",
        str(output),
    ]

    timeout = max(
        1200,
        int(duration * 30),
    )

    r = run_cmd(
        cmd,
        timeout,
    )

    if (
        r.returncode != 0
        or not output.exists()
        or output.stat().st_size == 0
    ):

        error_text = (
            r.stderr[-5000:]
            if r.stderr
            else "FFmpeg noma'lum xato."
        )

        raise RuntimeError(
            error_text
        )

    out_width, out_height = (
        ffprobe_dimensions(output)
    )

    if not out_width or not out_height:
        raise RuntimeError(
            "Tayyor video o'lchami aniqlanmadi."
        )

    logger.info(
        "Video encoded: "
        "%sx%s -> %sx%s | %s bytes",
        width,
        height,
        out_width,
        out_height,
        output.stat().st_size,
    )

    return (
        duration,
        out_width,
        out_height,
    )


# =========================================================
# SHAZAM AUDIO
# =========================================================

def extract_shazam_audio(
    source: Path,
    output: Path,
):

    r = run_cmd(
        [
            "ffmpeg",

            "-y",

            "-i",
            str(source),

            "-map",
            "0:a:0",

            "-vn",

            # 90 soniya
            "-t",
            "90",

            "-ac",
            "1",

            "-ar",
            "44100",

            "-c:a",
            "mp3",

            "-b:a",
            "128k",

            str(output),
        ],
        300,
    )

    if (
        r.returncode != 0
        or not output.exists()
        or output.stat().st_size == 0
    ):
        raise RuntimeError(
            r.stderr[-2500:]
        )


# =========================================================
# MP3
# =========================================================

def extract_mp3(
    source,
    output,
):

    r = run_cmd(
        [
            "ffmpeg",

            "-y",

            "-i",
            str(source),

            "-map",
            "0:a:0",

            "-vn",

            "-c:a",
            "libmp3lame",

            "-b:a",
            "128k",

            "-ar",
            "44100",

            str(output),
        ],
        600,
    )

    if (
        r.returncode != 0
        or not output.exists()
        or output.stat().st_size == 0
    ):
        raise RuntimeError(
            r.stderr[-2500:]
        )


# =========================================================
# SHAZAM RECOGNITION
# =========================================================

async def shazam_track(
    source: Path,
):

    try:

        result = await Shazam().recognize(
            str(source)
        )

        track = (
            result.get("track")
            or {}
        )

        title = track.get(
            "title"
        )

        artist = (
            track.get("subtitle")
            or track.get("artist")
        )

        if title:

            return (
                artist or "Noma'lum",
                title,
            )

    except Exception:
        logger.exception(
            "Shazam recognize xatosi"
        )

    return None, None


# =========================================================
# START
# =========================================================

async def start(
    update,
    context,
):

    await update.message.reply_text(
        "👋 Salom!\n\n"

        "📥 Media yuklash uchun "
        "havolani yuboring.\n\n"

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

        "🎶 Qo'shiq yoki ijrochi nomini "
        "ham yuborishingiz mumkin."
    )


# =========================================================
# CLEANUP
# =========================================================

async def cleanup_job(
    context,
    job_id,
    delay=1800,
):

    await asyncio.sleep(
        delay
    )

    job = (
        context.bot_data
        .setdefault(
            "media_jobs",
            {},
        )
        .pop(
            job_id,
            None,
        )
    )

    if job:

        shutil.rmtree(
            job["dir"],
            ignore_errors=True,
        )


# =========================================================
# URL PROCESS
# =========================================================

async def process_url(
    update,
    context,
    url,
):

    message = update.message

    work = (
        DOWNLOAD_DIR
        / uuid.uuid4().hex
    )

    work.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        await message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        downloaded, title = (
            await asyncio.to_thread(
                download_url,
                url,
                work,
            )
        )

        if (
            not downloaded
            or not downloaded.exists()
        ):
            raise RuntimeError(
                "Video fayli topilmadi."
            )

        original = (
            work
            / f"original{downloaded.suffix.lower()}"
        )

        shutil.copy2(
            downloaded,
            original,
        )

        # =================================================
        # SHAZAM
        # =================================================

        artist = None
        song = None

        if has_audio(
            original
        ):

            shazam_audio = (
                work
                / "shazam.mp3"
            )

            try:

                await asyncio.to_thread(
                    extract_shazam_audio,
                    original,
                    shazam_audio,
                )

                artist, song = (
                    await shazam_track(
                        shazam_audio
                    )
                )

            except Exception:

                logger.exception(
                    "URL Shazam xatosi"
                )

        # =================================================
        # VIDEO ENCODE
        # =================================================

        encoded = (
            work
            / "video_final.mp4"
        )

        await asyncio.to_thread(
            compress_video,
            downloaded,
            encoded,
        )

        if (
            encoded.stat().st_size
            > MAX_UPLOAD_BYTES
        ):

            raise RuntimeError(
                "Tayyor video 100 MB dan katta."
            )

        # =================================================
        # JOB
        # =================================================

        job_id = (
            uuid.uuid4().hex[:16]
        )

        context.bot_data.setdefault(
            "media_jobs",
            {},
        )[job_id] = {

            "audio_source":
                str(original),

            "dir":
                str(work),

            "artist":
                artist,

            "song":
                song,
        }

        # =================================================
        # BUTTONS
        # =================================================

        keyboard = None

        if has_audio(
            original
        ):

            buttons = [

                InlineKeyboardButton(
                    "🎵 MP3 yuklash",
                    callback_data=
                        f"jobmp3|{job_id}",
                )
            ]

            # Agar Shazam qo'shiqni aniqlasa
            if artist and song:

                buttons.append(

                    InlineKeyboardButton(
                        "🎶 To'liq qo'shiq",
                        callback_data=
                            f"fullsong|{job_id}",
                    )
                )

            keyboard = (
                InlineKeyboardMarkup(
                    [buttons]
                )
            )

        # =================================================
        # CAPTION
        # =================================================

        if artist and song:

            caption = (
                f"🎵 {artist} — {song}\n\n"
                f"{CAPTION}"
            )

        else:
            caption = CAPTION

        await message.chat.send_action(
            ChatAction.UPLOAD_VIDEO
        )

        with open(
            encoded,
            "rb",
        ) as video_fh:

            await message.reply_video(

                video=InputFile(
                    video_fh,
                    filename=
                        f"{safe_name(title)}.mp4",
                ),

                caption=caption,

                supports_streaming=True,

                reply_markup=keyboard,
            )

        asyncio.create_task(
            cleanup_job(
                context,
                job_id,
            )
        )

    except Exception as e:

        logger.exception(
            "URL processing xatosi"
        )

        await message.reply_text(
            "❌ Videoni yuklashda xatolik:\n\n"
            f"{str(e)[:3000]}"
        )

        shutil.rmtree(
            work,
            ignore_errors=True,
        )


# =========================================================
# YOUTUBE SONG SEARCH
# =========================================================

def youtube_song_search(
    query,
    limit=10,
):

    folder = (
        DOWNLOAD_DIR
        / (
            "search_"
            + uuid.uuid4().hex
        )
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        opts = yt_opts(
            folder
        )

        opts.update(
            {
                "skip_download":
                    True,

                "extract_flat":
                    True,

                "quiet":
                    True,

                "no_warnings":
                    True,
            }
        )

        with yt_dlp.YoutubeDL(
            opts
        ) as ydl:

            info = ydl.extract_info(
                f"ytsearch{limit}:{query}",
                download=False,
            )

        results = []

        for item in (
            info.get("entries")
            or []
        ):

            if not item:
                continue

            url = (
                item.get(
                    "webpage_url"
                )
                or item.get("url")
            )

            title = (
                item.get("title")
                or "Noma'lum"
            )

            if url:

                results.append(
                    {
                        "url":
                            url,

                        "title":
                            title,
                    }
                )

        return results

    finally:

        shutil.rmtree(
            folder,
            ignore_errors=True,
        )


# =========================================================
# SONG SEARCH RESULTS
# =========================================================

async def send_song_search_results(
    update,
    context,
    text,
):

    await update.message.reply_text(
        "🔎 YouTube'dan qo'shiq "
        "qidirilmoqda..."
    )

    results = (
        await asyncio.to_thread(
            youtube_song_search,
            text,
            10,
        )
    )

    if not results:

        await update.message.reply_text(
            "❌ Qo'shiq topilmadi."
        )

        return

    jobs = (
        context.bot_data
        .setdefault(
            "song_search_jobs",
            {},
        )
    )

    buttons = []

    for item in results:

        job_id = (
            uuid.uuid4()
            .hex[:12]
        )

        jobs[job_id] = item

        buttons.append(
            [

                InlineKeyboardButton(

                    f"🎵 "
                    f"{item['title'][:55]}",

                    callback_data=
                        f"searchmp3|{job_id}",
                )
            ]
        )

    await update.message.reply_text(

        "🎵 Topilgan qo'shiqlar:\n\n"
        "Keraklisini tanlang:",

        reply_markup=
            InlineKeyboardMarkup(
                buttons
            ),
    )


# =========================================================
# SEARCH SONG MP3
# =========================================================

async def search_song_mp3(
    update,
    context,
):

    q = update.callback_query

    await q.answer(
        "⏳ MP3 yuklanmoqda..."
    )

    job_id = (
        (q.data or "")
        .split("|", 1)[-1]
    )

    job = (
        context.bot_data
        .setdefault(
            "song_search_jobs",
            {},
        )
        .get(job_id)
    )

    if not job:

        await q.message.reply_text(
            "❌ Qidiruv sessiyasi tugagan. "
            "Qo'shiq nomini qaytadan yuboring."
        )

        return

    work = (
        DOWNLOAD_DIR
        / uuid.uuid4().hex
    )

    work.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        opts = yt_opts(
            work
        )

        opts["format"] = (
            "bestaudio/best"
        )

        with yt_dlp.YoutubeDL(
            opts
        ) as ydl:

            info = (
                await asyncio.to_thread(
                    ydl.extract_info,
                    job["url"],
                    download=True,
                )
            )

            downloaded = Path(
                ydl.prepare_filename(
                    info
                )
            )

        if not downloaded.exists():

            downloaded = (
                find_downloaded_file(
                    work
                )
            )

        if not downloaded:

            raise RuntimeError(
                "Audio fayl topilmadi."
            )

        output = (
            work / "song.mp3"
        )

        await asyncio.to_thread(
            extract_mp3,
            downloaded,
            output,
        )

        with open(
            output,
            "rb",
        ) as fh:

            await q.message.reply_audio(

                audio=InputFile(
                    fh,
                    filename=
                        f"{safe_name(job['title'])}.mp3",
                ),

                caption=CAPTION,
            )

    except Exception as e:

        logger.exception(
            "Qidirilgan qo'shiq MP3 xatosi"
        )

        await q.message.reply_text(
            "❌ MP3 yuklashda xatolik:\n\n"
            f"{str(e)[:1500]}"
        )

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True,
        )


# =========================================================
# TO'LIQ QO'SHIQ
# =========================================================

async def full_song_callback(
    update,
    context,
):

    q = update.callback_query

    await q.answer(
        "⏳ To'liq qo'shiq qidirilmoqda..."
    )

    job_id = (
        (q.data or "")
        .split("|", 1)[-1]
    )

    job = (
        context.bot_data
        .setdefault(
            "media_jobs",
            {},
        )
        .get(job_id)
    )

    if not job:

        await q.message.reply_text(
            "❌ Qo'shiq sessiyasi tugagan. "
            "Havolani qaytadan yuboring."
        )

        return

    artist = job.get(
        "artist"
    )

    song = job.get(
        "song"
    )

    if not song:

        await q.message.reply_text(
            "❌ Qo'shiq nomi aniqlanmadi."
        )

        return

    search_text = (
        f"{artist or ''} {song}"
    ).strip()

    work = (
        DOWNLOAD_DIR
        / uuid.uuid4().hex
    )

    work.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        await q.message.reply_text(
            "🔎 To'liq qo'shiq "
            "qidirilmoqda:\n"
            f"🎵 {search_text}"
        )

        # 5 ta natija olamiz
        results = (
            await asyncio.to_thread(
                youtube_song_search,
                search_text,
                5,
            )
        )

        if not results:

            await q.message.reply_text(
                "❌ To'liq qo'shiq "
                "YouTube'dan topilmadi."
            )

            return

        # Birinchi natijani yuklaymiz
        selected = results[0]

        opts = yt_opts(
            work
        )

        opts["format"] = (
            "bestaudio/best"
        )

        with yt_dlp.YoutubeDL(
            opts
        ) as ydl:

            info = (
                await asyncio.to_thread(
                    ydl.extract_info,
                    selected["url"],
                    download=True,
                )
            )

            downloaded = Path(
                ydl.prepare_filename(
                    info
                )
            )

        if not downloaded.exists():

            downloaded = (
                find_downloaded_file(
                    work
                )
            )

        if not downloaded:

            raise RuntimeError(
                "To'liq audio fayl topilmadi."
            )

        output = (
            work / "full_song.mp3"
        )

        await asyncio.to_thread(
            extract_mp3,
            downloaded,
            output,
        )

        filename = (
            f"{safe_name(search_text)}.mp3"
        )

        with open(
            output,
            "rb",
        ) as fh:

            await q.message.reply_audio(

                audio=InputFile(
                    fh,
                    filename=filename,
                ),

                caption=(
                    f"🎵 {search_text}\n\n"
                    f"{CAPTION}"
                ),
            )

    except Exception as e:

        logger.exception(
            "To'liq qo'shiq xatosi"
        )

        await q.message.reply_text(
            "❌ To'liq qo'shiqni "
            "yuklashda xatolik:\n\n"
            f"{str(e)[:2000]}"
        )

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True,
        )


# =========================================================
# TEXT / URL
# =========================================================

async def url_message(
    update,
    context,
):

    text = (
        update.message.text
        or ""
    ).strip()

    # =====================================================
    # URL
    # =====================================================

    if is_url(text):

        if is_supported_url(
            text
        ):

            # YouTube
            if (
                "youtube.com"
                in text.lower()

                or

                "youtu.be"
                in text.lower()
            ):

                context.user_data[
                    "pending_youtube"
                ] = text

                await update.message.reply_text(

                    "Qaysi format kerak?",

                    reply_markup=
                        InlineKeyboardMarkup(
                            [[

                                InlineKeyboardButton(
                                    "🎬 VIDEO",
                                    callback_data=
                                        "ytvideo",
                                ),

                                InlineKeyboardButton(
                                    "🎵 MP3",
                                    callback_data=
                                        "ytmp3",
                                ),

                            ]]
                        ),
                )

            else:

                await process_url(
                    update,
                    context,
                    text,
                )

        else:

            await update.message.reply_text(
                "🔎 Bu havola "
                "qo'llab-quvvatlanadigan "
                "saytlar ro'yxatida yo'q."
            )

        return

    # =====================================================
    # SONG SEARCH
    # =====================================================

    if len(text) >= 2:

        try:

            await send_song_search_results(
                update,
                context,
                text,
            )

        except Exception as e:

            logger.exception(
                "YouTube qo'shiq "
                "qidiruv xatosi"
            )

            await update.message.reply_text(
                "❌ Qo'shiq qidirishda "
                "xatolik:\n"
                f"{str(e)[:1500]}"
            )

    else:

        await update.message.reply_text(
            "🎵 Qo'shiq yoki ijrochi "
            "nomini yuboring."
        )


# =========================================================
# YOUTUBE DOWNLOAD
# =========================================================

async def youtube_download(
    update,
    context,
    mode,
):

    q = update.callback_query

    await q.answer()

    url = (
        context.user_data.get(
            "pending_youtube"
        )
    )

    if not url:

        await q.message.reply_text(
            "❌ Havola topilmadi. "
            "Qaytadan yuboring."
        )

        return

    work = (
        DOWNLOAD_DIR
        / uuid.uuid4().hex
    )

    work.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        await q.message.reply_text(
            "⏳ Yuklanmoqda..."
        )

        downloaded, title = (
            await asyncio.to_thread(
                download_url,
                url,
                work,
            )
        )

        if (
            not downloaded
            or not downloaded.exists()
        ):

            raise RuntimeError(
                "Yuklangan fayl topilmadi."
            )

        # =================================================
        # YOUTUBE MP3
        # =================================================

        if mode == "mp3":

            output = (
                work / "audio.mp3"
            )

            await asyncio.to_thread(
                extract_mp3,
                downloaded,
                output,
            )

            with open(
                output,
                "rb",
            ) as fh:

                await q.message.reply_audio(

                    audio=InputFile(
                        fh,
                        filename=
                            f"{safe_name(title)}.mp3",
                    ),

                    caption=CAPTION,
                )

            shutil.rmtree(
                work,
                ignore_errors=True,
            )

            return

        # =================================================
        # ORIGINAL
        # =================================================

        original = (
            work
            / f"original{downloaded.suffix.lower()}"
        )

        shutil.copy2(
            downloaded,
            original,
        )

        # =================================================
        # SHAZAM
        # =================================================

        artist = None
        song = None

        if has_audio(
            original
        ):

            shazam_audio = (
                work
                / "shazam.mp3"
            )

            try:

                await asyncio.to_thread(
                    extract_shazam_audio,
                    original,
                    shazam_audio,
                )

                artist, song = (
                    await shazam_track(
                        shazam_audio
                    )
                )

            except Exception:

                logger.exception(
                    "YouTube Shazam xatosi"
                )

        # =================================================
        # ENCODE
        # =================================================

        encoded = (
            work
            / "video_final.mp4"
        )

        await asyncio.to_thread(
            compress_video,
            downloaded,
            encoded,
        )

        if (
            encoded.stat().st_size
            > MAX_UPLOAD_BYTES
        ):

            raise RuntimeError(
                "Tayyor video 100 MB dan katta."
            )

        # =================================================
        # JOB
        # =================================================

        job_id = (
            uuid.uuid4().hex[:16]
        )

        context.bot_data.setdefault(
            "media_jobs",
            {},
        )[job_id] = {

            "audio_source":
                str(original),

            "dir":
                str(work),

            "artist":
                artist,

            "song":
                song,
        }

        # =================================================
        # BUTTONS
        # =================================================

        keyboard = None

        if has_audio(
            original
        ):

            buttons = [

                InlineKeyboardButton(
                    "🎵 MP3 yuklash",
                    callback_data=
                        f"jobmp3|{job_id}",
                )
            ]

            if artist and song:

                buttons.append(

                    InlineKeyboardButton(
                        "🎶 To'liq qo'shiq",
                        callback_data=
                            f"fullsong|{job_id}",
                    )
                )

            keyboard = (
                InlineKeyboardMarkup(
                    [buttons]
                )
            )

        # =================================================
        # CAPTION
        # =================================================

        if artist and song:

            caption = (
                f"🎵 {artist} — {song}\n\n"
                f"{CAPTION}"
            )

        else:

            caption = CAPTION

        with open(
            encoded,
            "rb",
        ) as video_fh:

            await q.message.reply_video(

                video=InputFile(
                    video_fh,
                    filename=
                        f"{safe_name(title)}.mp4",
                ),

                caption=caption,

                supports_streaming=True,

                reply_markup=keyboard,
            )

        asyncio.create_task(
            cleanup_job(
                context,
                job_id,
            )
        )

    except Exception as e:

        logger.exception(
            "YouTube xatosi"
        )

        await q.message.reply_text(
            "❌ YouTube yuklashda "
            "xatolik:\n\n"
            f"{str(e)[:3000]}"
        )

        shutil.rmtree(
            work,
            ignore_errors=True,
        )


# =========================================================
# CALLBACKS
# =========================================================

async def media_callback(
    update,
    context,
):

    q = update.callback_query

    data = (
        q.data or ""
    )

    # YouTube VIDEO
    if data == "ytvideo":

        await youtube_download(
            update,
            context,
            "video",
        )

    # YouTube MP3
    elif data == "ytmp3":

        await youtube_download(
            update,
            context,
            "mp3",
        )

    # Video ichidagi MP3
    elif data.startswith(
        "jobmp3|"
    ):

        await q.answer(
            "⏳ MP3 tayyorlanmoqda..."
        )

        job_id = data.split(
            "|",
            1,
        )[1]

        job = (
            context.bot_data
            .setdefault(
                "media_jobs",
                {},
            )
            .get(job_id)
        )

        if not job:

            await q.message.reply_text(
                "❌ Bu video sessiyasi "
                "tugagan. Videoni qaytadan "
                "yuboring."
            )

            return

        source = Path(
            job["audio_source"]
        )

        work = Path(
            job["dir"]
        )

        try:

            if not source.exists():

                raise RuntimeError(
                    "Original audio "
                    "topilmadi."
                )

            output = (
                work
                / "extracted.mp3"
            )

            await asyncio.to_thread(
                extract_mp3,
                source,
                output,
            )

            artist = job.get(
                "artist"
            )

            song = job.get(
                "song"
            )

            if artist and song:

                filename = (
                    f"{safe_name(artist + ' - ' + song)}.mp3"
                )

            else:

                filename = (
                    "audio.mp3"
                )

            with open(
                output,
                "rb",
            ) as audio_fh:

                await q.message.reply_audio(

                    audio=InputFile(
                        audio_fh,
                        filename=filename,
                    ),

                    caption=CAPTION,
                )

        except Exception as e:

            logger.exception(
                "MP3 callback xatosi"
            )

            await q.message.reply_text(
                "❌ MP3 tayyorlashda "
                "xatolik:\n"
                f"{str(e)[:1500]}"
            )


# =========================================================
# RECOGNIZE AUDIO
# =========================================================

async def recognize_audio(
    update,
    context,
):

    message = update.message

    work = (
        DOWNLOAD_DIR
        / uuid.uuid4().hex
    )

    work.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        await message.reply_text(
            "🎧 Musiqa aniqlanmoqda..."
        )

        source = (
            work / "input"
        )

        # Voice
        if message.voice:

            f = (
                await message.voice.get_file()
            )

            await f.download_to_drive(
                str(source)
                + ".ogg"
            )

            source = Path(
                str(source)
                + ".ogg"
            )

        # Audio
        elif message.audio:

            f = (
                await message.audio.get_file()
            )

            await f.download_to_drive(
                str(source)
                + ".mp3"
            )

            source = Path(
                str(source)
                + ".mp3"
            )

        # Video
        elif message.video:

            f = (
                await message.video.get_file()
            )

            await f.download_to_drive(
                str(source)
                + ".mp4"
            )

            source = Path(
                str(source)
                + ".mp4"
            )

        # Video note
        elif message.video_note:

            f = (
                await message.video_note.get_file()
            )

            await f.download_to_drive(
                str(source)
                + ".mp4"
            )

            source = Path(
                str(source)
                + ".mp4"
            )

        else:

            await message.reply_text(
                "❌ Audio yoki video "
                "topilmadi."
            )

            return

        # =================================================
        # SHAZAM SOURCE
        # =================================================

        shazam_source = source

        if (
            message.video
            or message.video_note
        ):

            shazam_source = (
                work
                / "shazam.mp3"
            )

            await asyncio.to_thread(
                extract_shazam_audio,
                source,
                shazam_source,
            )

        # =================================================
        # RECOGNIZE
        # =================================================

        artist, title = (
            await shazam_track(
                shazam_source
            )
        )

        if not title:

            await message.reply_text(
                "❌ Qo'shiq aniqlanmadi."
            )

            return

        artist_name = (
            artist
            or "Noma'lum"
        )

        context.user_data[
            "song_search"
        ] = (
            f"{artist_name} {title}"
            .strip()
        )

        await message.reply_text(

            f"🎵 Qo'shiq topildi!\n\n"

            f"👤 Artist: "
            f"{artist_name}\n"

            f"🎶 Qo'shiq: "
            f"{title}",

            reply_markup=
                InlineKeyboardMarkup(
                    [[

                        InlineKeyboardButton(
                            "🎵 MP3",
                            callback_data=
                                "songmp3",
                        )

                    ]]
                ),
        )

    except Exception:

        logger.exception(
            "Shazam xatosi"
        )

        await message.reply_text(
            "❌ Qo'shiqni aniqlab "
            "bo'lmadi."
        )

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True,
        )


# =========================================================
# SONG CALLBACK
# =========================================================

async def song_callback(
    update,
    context,
):

    q = update.callback_query

    await q.answer(
        "⏳ Yuklanmoqda..."
    )

    search = (
        context.user_data.get(
            "song_search"
        )
    )

    if not search:

        await q.message.reply_text(
            "❌ Qo'shiq ma'lumoti "
            "topilmadi."
        )

        return

    work = (
        DOWNLOAD_DIR
        / uuid.uuid4().hex
    )

    work.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        opts = yt_opts(
            work
        )

        opts["format"] = (
            "bestaudio/best"
        )

        with yt_dlp.YoutubeDL(
            opts
        ) as ydl:

            info = (
                await asyncio.to_thread(
                    ydl.extract_info,
                    f"ytsearch1:{search}",
                    True,
                )
            )

            entries = (
                info.get("entries")
                or []
            )

            if not entries:

                raise RuntimeError(
                    "Qo'shiq topilmadi."
                )

            downloaded = Path(
                ydl.prepare_filename(
                    entries[0]
                )
            )

        if not downloaded.exists():

            downloaded = (
                find_downloaded_file(
                    work
                )
            )

        if not downloaded:

            raise RuntimeError(
                "Audio fayl topilmadi."
            )

        output = (
            work
            / "song.mp3"
        )

        await asyncio.to_thread(
            extract_mp3,
            downloaded,
            output,
        )

        with open(
            output,
            "rb",
        ) as fh:

            await q.message.reply_audio(

                audio=InputFile(
                    fh,
                    filename="song.mp3",
                ),

                caption=CAPTION,
            )

    except Exception:

        logger.exception(
            "Song download xatosi"
        )

        await q.message.reply_text(
            "❌ Qo'shiqni yuklab "
            "bo'lmadi."
        )

    finally:

        shutil.rmtree(
            work,
            ignore_errors=True,
        )


# =========================================================
# ERROR
# =========================================================

async def error_handler(
    update,
    context,
):

    logger.exception(
        "Unhandled exception",
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN topilmadi. "
            "Railway Variables ichiga "
            "BOT_TOKEN qo'ying."
        )

    app = (
        Application
        .builder()
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

    # YouTube video / MP3 / video MP3
    app.add_handler(
        CallbackQueryHandler(
            media_callback,
            pattern=
                r"^(ytvideo|ytmp3|jobmp3\|.+)$",
        )
    )

    # Song MP3
    app.add_handler(
        CallbackQueryHandler(
            song_callback,
            pattern=
                r"^songmp3$",
        )
    )

    # Search result MP3
    app.add_handler(
        CallbackQueryHandler(
            search_song_mp3,
            pattern=
                r"^searchmp3\|.+$",
        )
    )

    # To'liq qo'shiq
    app.add_handler(
        CallbackQueryHandler(
            full_song_callback,
            pattern=
                r"^fullsong\|.+$",
        )
    )

    # Audio / video / voice
    app.add_handler(
        MessageHandler(
            filters.VOICE
            | filters.AUDIO
            | filters.VIDEO
            | filters.VIDEO_NOTE,
            recognize_audio,
        )
    )

    # Text / URL
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
        "Bot ishga tushdi"
    )

    app.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# START BOT
# =========================================================

if __name__ == "__main__":
    main()
