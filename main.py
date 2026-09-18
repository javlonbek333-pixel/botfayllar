import os
import re
import asyncio
import base64
import subprocess
import logging
from pathlib import Path

import yt_dlp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.constants import ChatAction

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# SOZLAMALAR
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Telegramga yuboriladigan maksimal video
TARGET_SIZE = 14 * 1024 * 1024

# Manbadan yuklanadigan maksimal fayl
MAX_SOURCE_SIZE = 1024 * 1024 * 1024

WORKDIR = Path("/tmp/bot_downloads")
WORKDIR.mkdir(parents=True, exist_ok=True)

COOKIES_FILE = WORKDIR / "youtube_cookies.txt"


# =========================================================
# YOUTUBE COOKIES
# =========================================================

def prepare_cookies():
    """
    Railway Variables ichidan YouTube cookies olish.

    YOUTUBE_COOKIES_B64 ishlatilsa:
    cookie fayli Base64 ko'rinishida saqlanadi.

    YOUTUBE_COOKIES ishlatilsa:
    cookie faylining oddiy matni saqlanadi.
    """

    b64 = os.getenv("YOUTUBE_COOKIES_B64", "").strip()
    raw = os.getenv("YOUTUBE_COOKIES", "")

    try:

        if b64:

            COOKIES_FILE.write_bytes(
                base64.b64decode(b64)
            )

            logger.info(
                "YouTube cookies loaded from B64."
            )

        elif raw.strip():

            COOKIES_FILE.write_text(
                raw,
                encoding="utf-8"
            )

            logger.info(
                "YouTube cookies loaded."
            )

    except Exception:

        logger.exception(
            "YouTube cookies yuklanmadi."
        )


# =========================================================
# YT-DLP ASOSIY SOZLAMALARI
# =========================================================

def ydl_options():

    options = {

        "quiet": False,

        "no_warnings": False,

        "noplaylist": True,

        "retries": 3,

        "fragment_retries": 3,

        "socket_timeout": 30,

        # Yuklashni tezlashtirish
        "concurrent_fragment_downloads": 8,

        # Maksimal source
        "max_filesize": MAX_SOURCE_SIZE,

        # Fayl nomi
        "outtmpl": str(
            WORKDIR / "%(id)s.%(ext)s"
        ),

        "restrictfilenames": True,
    }

    # Cookie mavjud bo'lsa
    if (
        COOKIES_FILE.exists()
        and COOKIES_FILE.stat().st_size > 0
    ):

        options["cookiefile"] = str(
            COOKIES_FILE
        )

    return options


# =========================================================
# FAYLLARNI O'CHIRISH
# =========================================================

def cleanup(*files):

    for file in files:

        if not file:
            continue

        try:

            Path(file).unlink(
                missing_ok=True
            )

        except Exception:

            pass


# =========================================================
# URL TEKSHIRISH
# =========================================================

def is_url(text):

    return bool(
        re.match(
            r"^https?://",
            text.strip(),
            re.I
        )
    )


# =========================================================
# MANBA ANIQLASH
# =========================================================

def source_name(url):

    u = url.lower()

    if (
        "youtube.com" in u
        or "youtu.be" in u
    ):
        return "YouTube"

    if "instagram.com" in u:
        return "Instagram"

    if (
        "tiktok.com" in u
        or "vm.tiktok.com" in u
    ):
        return "TikTok"

    if (
        "facebook.com" in u
        or "fb.watch" in u
    ):
        return "Facebook"

    if (
        "ok.ru" in u
        or "odnoklassniki.ru" in u
    ):
        return "OK.ru"

    if "pinterest." in u:
        return "Pinterest"

    if "snapchat.com" in u:
        return "Snapchat"

    if "likee.video" in u:
        return "Likee"

    if "threads.net" in u:
        return "Threads"

    return "Media"


# =========================================================
# VIDEO YUKLASH
# =========================================================

def download_video(url):

    options = ydl_options()

    options.update({

        # Faqat 480p gacha
        "format": (
            "bestvideo[height<=480]+bestaudio/"
            "best[height<=480]/"
            "bestvideo+bestaudio/"
            "best"
        ),

        "merge_output_format": "mp4",
    })

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        filename = Path(
            ydl.prepare_filename(info)
        )

        # MP4 bo'lsa
        mp4_file = filename.with_suffix(
            ".mp4"
        )

        if mp4_file.exists():

            filename = mp4_file

        return str(filename), info


# =========================================================
# MP3 YUKLASH
# =========================================================

def download_mp3(url):

    options = ydl_options()

    options.update({

        # Video emas, faqat audio
        "format": (
            "bestaudio[ext=m4a]/"
            "bestaudio/best"
        ),

        "postprocessors": [

            {
                "key": "FFmpegExtractAudio",

                "preferredcodec": "mp3",

                "preferredquality": "128",
            }

        ],
    })

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        filename = Path(
            ydl.prepare_filename(info)
        ).with_suffix(".mp3")

        return str(filename), info


# =========================================================
# VIDEO 480P + 14 MB GACHA SIQISH
# =========================================================

def compress_14mb(input_file):

    input_file = Path(input_file)

    output = input_file.with_name(
        input_file.stem + "_480p.mp4"
    )

    # Video davomiyligini aniqlash
    probe = subprocess.run(

        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_file),
        ],

        capture_output=True,

        text=True,

        timeout=30,
    )

    try:

        duration = float(
            probe.stdout.strip()
        )

    except Exception:

        duration = 0


    # Bir necha bitrate bilan urinib ko'ramiz
    for video_kbps in (
        1100,
        900,
        750,
        600,
        500,
        400,
        300,
    ):

        # Audio bitrate
        if duration > 900:

            audio_kbps = 64

        else:

            audio_kbps = 96


        # Video juda uzun bo'lsa
        # avtomatik pastroq bitrate
        if duration > 0:

            total_kbits = (
                TARGET_SIZE * 8 / 1000
            )

            calculated = int(
                total_kbits / duration
                - audio_kbps
                - 10
            )

            video_kbps = min(
                video_kbps,
                max(250, calculated)
            )


        command = [

            "ffmpeg",

            "-y",

            "-i",
            str(input_file),

            # Maksimal kenglik 480
            "-vf",
            "scale='min(480,iw)':-2",

            # H.264
            "-c:v",
            "libx264",

            # Tez encoding
            "-preset",
            "ultrafast",

            "-b:v",
            f"{video_kbps}k",

            "-maxrate",
            f"{video_kbps}k",

            "-bufsize",
            f"{video_kbps * 2}k",

            # Audio
            "-c:a",
            "aac",

            "-b:a",
            f"{audio_kbps}k",

            # Telegram uchun qulay
            "-movflags",
            "+faststart",

            str(output),
        ]


        try:

            subprocess.run(

                command,

                check=True,

                stdout=subprocess.DEVNULL,

                stderr=subprocess.PIPE,
            )


            # 14 MiB dan kichik bo'lsa tayyor
            if (
                output.exists()
                and output.stat().st_size
                <= TARGET_SIZE
            ):

                logger.info(
                    "Video tayyor: %.2f MB",
                    output.stat().st_size
                    / 1024
                    / 1024,
                )

                return str(output)


        except Exception:

            logger.exception(
                "FFmpeg xatosi."
            )


    raise RuntimeError(
        "Video 14 MB gacha siqilmadi."
    )


# =========================================================
# VIDEO PROCESS
# =========================================================

async def process_video(
    message,
    url
):

    status = await message.reply_text(
        "⏳ Yuklanmoqda..."
    )

    source = None
    final = None

    try:

        # Yuklash
        source, info = await asyncio.to_thread(
            download_video,
            url
        )

        # Har doim 480p ga qayta encode
        await status.edit_text(
            "⚙️ 480p video tayyorlanmoqda..."
        )

        final = await asyncio.to_thread(
            compress_14mb,
            source
        )

        title = (
            info.get("title")
            or "Video"
        )

        await status.delete()

        await message.chat.send_action(
            ChatAction.UPLOAD_VIDEO
        )

        with open(
            final,
            "rb"
        ) as video:

            await message.reply_video(

                video=video,

                caption=(
                    f"🎬 {title}"
                )[:1024],

                supports_streaming=True,
            )


    except Exception:

        logger.exception(
            "VIDEO ERROR"
        )

        try:

            await status.edit_text(

                "❌ Video yuklanmadi.\n\n"
                "YouTube bo'lsa cookie kerak "
                "bo'lishi mumkin."
            )

        except Exception:

            pass


    finally:

        cleanup(
            source,
            final
        )


# =========================================================
# MP3 PROCESS
# =========================================================

async def process_mp3(
    message,
    url
):

    status = await message.reply_text(
        "⏳ Yuklanmoqda..."
    )

    mp3 = None

    try:

        mp3, info = await asyncio.to_thread(
            download_mp3,
            url
        )

        title = (
            info.get("title")
            or "Audio"
        )

        await status.delete()

        await message.chat.send_action(
            ChatAction.UPLOAD_AUDIO
        )

        with open(
            mp3,
            "rb"
        ) as audio:

            await message.reply_audio(

                audio=audio,

                title=title[:64],

                performer=(
                    info.get("artist")
                    or info.get("uploader")
                ),
            )


    except Exception:

        logger.exception(
            "MP3 ERROR"
        )

        try:

            await status.edit_text(

                "❌ MP3 yuklanmadi.\n\n"
                "YouTube bo'lsa cookie kerak "
                "bo'lishi mumkin."
            )

        except Exception:

            pass


    finally:

        cleanup(mp3)


# =========================================================
# YOUTUBE BUTTONLARI
# =========================================================

def youtube_buttons():

    return InlineKeyboardMarkup(

        [

            [

                InlineKeyboardButton(
                    "🎬 VIDEO 480p",
                    callback_data="yt_video"
                ),

                InlineKeyboardButton(
                    "🎵 MP3 128 kbps",
                    callback_data="yt_mp3"
                ),

            ]

        ]

    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "👋 Havolani yuboring.\n\n"

        "YouTube • Instagram • TikTok • "
        "Facebook • OK.ru • Pinterest • "
        "Snapchat • Likee • Threads\n\n"

        "YouTube uchun:\n"
        "🎬 VIDEO 480p\n"
        "🎵 MP3 128 kbps"
    )


# =========================================================
# ASOSIY MESSAGE HANDLER
# =========================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if (
        not update.message
        or not update.message.text
    ):

        return


    text = update.message.text.strip()


    # -----------------------------
    # SEARCH
    # -----------------------------

    if text.lower().startswith(
        (
            "search ",
            "youtube search "
        )
    ):

        query = re.sub(

            r"^(search|youtube search)\s+",

            "",

            text,

            flags=re.I
        ).strip()


        if query:

            await youtube_search(
                update,
                context,
                query
            )

        return


    # -----------------------------
    # URL
    # -----------------------------

    if not is_url(text):

        await update.message.reply_text(
            "🔗 Havolani yuboring."
        )

        return


    # -----------------------------
    # YOUTUBE
    # -----------------------------

    if source_name(text) == "YouTube":

        context.user_data[
            "youtube_url"
        ] = text

        await update.message.reply_text(

            "YouTube havolasi.\n"
            "Formatni tanlang:",

            reply_markup=(
                youtube_buttons()
            )
        )

        return


    # -----------------------------
    # BOSHQA SAYTLAR
    # -----------------------------

    await process_video(
        update.message,
        text
    )


# =========================================================
# CALLBACK HANDLER
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()


    # MUHIM:
    # CallbackQuery uchun from_user
    user_id = query.from_user.id

    logger.info(
        "Callback user=%s data=%s",
        user_id,
        query.data
    )


    # =====================================================
    # YOUTUBE VIDEO
    # =====================================================

    if query.data == "yt_video":

        url = context.user_data.get(
            "youtube_url"
        )

        if not url:

            await query.message.reply_text(
                "❌ Havola topilmadi."
            )

            return


        await query.edit_message_text(
            "⏳ Yuklanmoqda..."
        )

        await process_video(
            query.message,
            url
        )

        return


    # =====================================================
    # YOUTUBE MP3
    # =====================================================

    if query.data == "yt_mp3":

        url = context.user_data.get(
            "youtube_url"
        )

        if not url:

            await query.message.reply_text(
                "❌ Havola topilmadi."
            )

            return


        await query.edit_message_text(
            "⏳ Yuklanmoqda..."
        )

        await process_mp3(
            query.message,
            url
        )

        return


    # =====================================================
    # SEARCH PAGE
    # =====================================================

    if query.data.startswith(
        "search_page:"
    ):

        page = int(
            query.data.split(":")[1]
        )

        await show_search_page(
            query,
            context,
            page
        )

        return


    # =====================================================
    # SEARCH RESULT
    # =====================================================

    if query.data.startswith(
        "search_pick:"
    ):

        index = int(
            query.data.split(":")[1]
        )

        results = context.user_data.get(
            "search_results",
            []
        )

        if index >= len(results):

            await query.message.reply_text(
                "❌ Natija topilmadi."
            )

            return


        await query.edit_message_text(
            "⏳ Yuklanmoqda..."
        )

        await process_mp3(

            query.message,

            results[index]["url"]
        )


# =========================================================
# YOUTUBE SEARCH 50 TA
# =========================================================

async def youtube_search(
    update,
    context,
    query_text
):

    status = await update.message.reply_text(
        "🔎 Qidirilmoqda..."
    )


    def search():

        options = ydl_options()

        options.update({

            "extract_flat": True,

            "skip_download": True,

            "quiet": True,
        })


        with yt_dlp.YoutubeDL(
            options
        ) as ydl:

            data = ydl.extract_info(

                f"ytsearch50:{query_text}",

                download=False
            )

            return data.get(
                "entries",
                []
            )


    try:

        entries = await asyncio.to_thread(
            search
        )

        results = []


        for entry in entries[:50]:

            if not entry:
                continue


            video_id = entry.get("id")

            if not video_id:
                continue


            url = (
                entry.get("webpage_url")
                or
                f"https://www.youtube.com/watch?v={video_id}"
            )


            results.append({

                "title": (
                    entry.get("title")
                    or "Nomsiz"
                ),

                "url": url,
            })


        context.user_data[
            "search_results"
        ] = results


        await status.delete()


        await show_search_page_from_message(

            update.message,

            context,

            0
        )


    except Exception:

        logger.exception(
            "SEARCH ERROR"
        )

        await status.edit_text(
            "❌ Qidirishda xatolik."
        )


# =========================================================
# SEARCH BUTTONLARI
# =========================================================

def build_search_markup(
    results,
    page
):

    start = page * 10

    end = min(
        start + 10,
        len(results)
    )


    buttons = []


    for i in range(
        start,
        end
    ):

        title = results[i][
            "title"
        ]

        buttons.append(

            [

                InlineKeyboardButton(

                    f"{i + 1}. "
                    f"{title[:55]}",

                    callback_data=(
                        f"search_pick:{i}"
                    )
                )

            ]

        )


    navigation = []


    if page > 0:

        navigation.append(

            InlineKeyboardButton(

                "⬅️ Oldingi",

                callback_data=(
                    f"search_page:{page - 1}"
                )
            )

        )


    if end < len(results):

        navigation.append(

            InlineKeyboardButton(

                "Keyingi ➡️",

                callback_data=(
                    f"search_page:{page + 1}"
                )
            )

        )


    if navigation:

        buttons.append(
            navigation
        )


    return InlineKeyboardMarkup(
        buttons
    )


# =========================================================
# SEARCH PAGE MESSAGE
# =========================================================

async def show_search_page_from_message(
    message,
    context,
    page
):

    results = context.user_data.get(
        "search_results",
        []
    )


    if not results:

        await message.reply_text(
            "❌ Natija topilmadi."
        )

        return


    start = page * 10

    end = min(
        start + 10,
        len(results)
    )


    await message.reply_text(

        f"🎵 Natijalar "
        f"{start + 1}-{end} / "
        f"{len(results)}",

        reply_markup=(
            build_search_markup(
                results,
                page
            )
        )
    )


# =========================================================
# SEARCH PAGE CALLBACK
# =========================================================

async def show_search_page(
    query,
    context,
    page
):

    results = context.user_data.get(
        "search_results",
        []
    )


    if not results:

        await query.message.reply_text(
            "❌ Natija topilmadi."
        )

        return


    start = page * 10

    end = min(
        start + 10,
        len(results)
    )


    await query.edit_message_text(

        f"🎵 Natijalar "
        f"{start + 1}-{end} / "
        f"{len(results)}",

        reply_markup=(
            build_search_markup(
                results,
                page
            )
        )
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):

    logger.error(
        "Unhandled error: %s",
        context.error,
        exc_info=True
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN Railway Variables ichida yo'q."
        )


    # Cookie tayyorlash
    prepare_cookies()


    # Telegram application
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
            start
        )
    )


    # Inline button
    app.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )


    # Text / URL
    app.add_handler(

        MessageHandler(

            filters.TEXT
            & ~filters.COMMAND,

            handle_message
        )

    )


    # Error
    app.add_error_handler(
        error_handler
    )


    logger.info(
        "Bot ishga tushdi."
    )


    app.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    main()
