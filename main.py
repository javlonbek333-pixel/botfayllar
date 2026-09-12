import os
import re
import uuid
import shutil
import logging
import subprocess

import yt_dlp

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==========================================
# SOZLAMALAR
# ==========================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")

DOWNLOAD_DIR = "/tmp/videos"

# Siqilgandan keyingi taxminiy maksimal hajm
TARGET_SIZE_MB = 45

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ==========================================
# LOG
# ==========================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)


# ==========================================
# /START
# ==========================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "👋 Assalomu alaykum!\n\n"
        "🎬 Men YouTube va Instagram videolarini yuklab beraman.\n\n"
        "🔗 Video havolasini yuboring."
    )


# ==========================================
# FAYL HAJMI
# ==========================================

def get_size_mb(filename):

    if not os.path.exists(filename):
        return 0

    return os.path.getsize(filename) / (
        1024 * 1024
    )


# ==========================================
# VIDEO DAVOMIYLIGI
# ==========================================

def get_duration(filename):

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        filename
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        return float(
            result.stdout.strip()
        )
    except Exception:
        return 60


# ==========================================
# VIDEO SIQISH
# ==========================================

def compress_video(
    input_file,
    output_file
):

    duration = get_duration(
        input_file
    )

    if duration <= 0:
        duration = 60

    # Maqsadli hajm
    target_bits = (
        TARGET_SIZE_MB
        * 8
        * 1024
        * 1024
    )

    # Audio bitrate
    audio_bitrate = 96000

    # Video bitrate
    video_bitrate = int(
        (
            target_bits / duration
        )
        - audio_bitrate
    )

    # Juda past bo'lib ketmasin
    if video_bitrate < 150000:
        video_bitrate = 150000

    # Juda katta bo'lib ketmasin
    if video_bitrate > 5000000:
        video_bitrate = 5000000

    command = [
        "ffmpeg",
        "-y",

        "-i",
        input_file,

        # Video
        "-c:v",
        "libx264",

        "-b:v",
        str(video_bitrate),

        "-maxrate",
        str(
            int(video_bitrate * 1.15)
        ),

        "-bufsize",
        str(
            int(video_bitrate * 2)
        ),

        # Tezlik
        "-preset",
        "veryfast",

        # Audio
        "-c:a",
        "aac",

        "-b:a",
        "96k",

        # MP4
        "-movflags",
        "+faststart",

        output_file
    ]

    logging.info(
        "FFmpeg ishga tushmoqda..."
    )

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:

        logging.error(
            "FFMPEG XATOSI:\n%s",
            result.stderr
        )

        raise Exception(
            "FFmpeg xatosi:\n"
            + result.stderr[-1500:]
        )

    if not os.path.exists(
        output_file
    ):

        raise Exception(
            "FFmpeg video fayl yaratmadi."
        )


# ==========================================
# YOUTUBE / INSTAGRAM TEKSHIRISH
# ==========================================

def is_supported_url(url):

    supported_domains = [
        "youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
        "instagram.com"
    ]

    url_lower = url.lower()

    return any(
        domain in url_lower
        for domain in supported_domains
    )


# ==========================================
# VIDEO YUKLASH
# ==========================================

async def download_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.message.text:
        return

    url = update.message.text.strip()

    # URL tekshirish
    if not re.match(
        r"^https?://",
        url
    ):

        await update.message.reply_text(
            "❌ Havola noto‘g‘ri.\n\n"
            "🔗 YouTube yoki Instagram "
            "video havolasini yuboring."
        )

        return

    # YouTube / Instagram
    if not is_supported_url(url):

        await update.message.reply_text(
            "❌ Bu havola qo‘llab-quvvatlanmaydi.\n\n"
            "Faqat YouTube va Instagram "
            "havolalarini yuboring."
        )

        return

    # Holat xabari
    status = await update.message.reply_text(
        "⏳ Video tayyorlanmoqda..."
    )

    # Har bir yuklashga alohida papka
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

    input_file = None

    output_file = os.path.join(
        work_dir,
        "compressed.mp4"
    )

    try:

        # ==================================
        # YUKLASH
        # ==================================

        await status.edit_text(
            "⬇️ Video yuklanmoqda..."
        )

        ydl_opts = {

            # 720p gacha
            # Railway xotirasini tejaydi
            "format": (
                "bestvideo[height<=720]"
                "[ext=mp4]+"
                "bestaudio[ext=m4a]/"

                "best[height<=720]"
                "[ext=mp4]/"

                "best[height<=720]/"

                "best"
            ),

            # Fayl nomi
            "outtmpl": os.path.join(
                work_dir,
                "original.%(ext)s"
            ),

            # MP4 ga birlashtirish
            "merge_output_format": "mp4",

            # Playlist yuklamaslik
            "noplaylist": True,

            # Qayta urinish
            "retries": 5,

            "fragment_retries": 5,

            # Internet timeout
            "socket_timeout": 60,

            # Log
            "quiet": True,

            "no_warnings": True,

            # YouTube
            "extractor_args": {
                "youtube": {
                    "player_client": [
                        "android",
                        "web"
                    ]
                }
            }
        }

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            prepared_file = (
                ydl.prepare_filename(info)
            )

        # ==================================
        # YUKLANGAN FAYLNI TOPISH
        # ==================================

        possible_files = []

        if os.path.exists(
            prepared_file
        ):

            possible_files.append(
                prepared_file
            )

        base = os.path.splitext(
            prepared_file
        )[0]

        for ext in [
            ".mp4",
            ".mkv",
            ".webm",
            ".mov",
            ".m4v"
        ]:

            candidate = (
                base + ext
            )

            if os.path.exists(
                candidate
            ):

                possible_files.append(
                    candidate
                )

        # Papkani tekshirish
        for filename in os.listdir(
            work_dir
        ):

            path = os.path.join(
                work_dir,
                filename
            )

            if os.path.isfile(path):

                if filename != "compressed.mp4":

                    possible_files.append(
                        path
                    )

        # Takrorlarni olib tashlash
        possible_files = list(
            dict.fromkeys(
                possible_files
            )
        )

        if not possible_files:

            raise Exception(
                "Yuklangan video "
                "fayli topilmadi."
            )

        # Eng katta faylni olish
        input_file = max(
            possible_files,
            key=os.path.getsize
        )

        original_size = get_size_mb(
            input_file
        )

        logging.info(
            "Asl video: %.2f MB",
            original_size
        )

        # ==================================
        # SIQISH
        # ==================================

        await status.edit_text(
            f"✅ Video yuklandi.\n\n"
            f"📦 Asl hajmi: "
            f"{original_size:.1f} MB\n\n"
            f"🔄 Hajmi kamaytirilmoqda..."
        )

        compress_video(
            input_file,
            output_file
        )

        compressed_size = get_size_mb(
            output_file
        )

        logging.info(
            "Siqilgan video: %.2f MB",
            compressed_size
        )

        # ==================================
        # YAKUNIY FAYL
        # ==================================

        if (
            compressed_size > 0
            and os.path.exists(output_file)
        ):

            final_file = output_file

            final_size = compressed_size

        else:

            final_file = input_file

            final_size = original_size

        # ==================================
        # TELEGRAMGA YUBORISH
        # ==================================

        await status.edit_text(
            "📤 Video Telegramga yuborilmoqda..."
        )

        with open(
            final_file,
            "rb"
        ) as video:

            await update.message.reply_video(
                video=video,

                caption=(
                    "🎬 Video tayyor!\n\n"
                    f"📦 Hajmi: "
                    f"{final_size:.1f} MB\n"
                    "✅ Siqildi"
                ),

                supports_streaming=True
            )

        # Statusni o'chirish
        await status.delete()

    except Exception as e:

        logging.exception(
            "BOT XATOSI"
        )

        error_text = str(e)

        if len(error_text) > 1500:

            error_text = (
                error_text[-1500:]
            )

        try:

            await status.edit_text(
                "❌ Xatolik yuz berdi.\n\n"
                f"{error_text}"
            )

        except Exception:

            pass

    finally:

        # ==================================
        # VAQTINCHALIK FAYLLARNI O'CHIRISH
        # ==================================

        try:

            if os.path.exists(
                work_dir
            ):

                shutil.rmtree(
                    work_dir,
                    ignore_errors=True
                )

        except Exception as e:

            logging.error(
                "Fayl o'chirish xatosi: %s",
                e
            )


# ==========================================
# BOTNI ISHGA TUSHIRISH
# ==========================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN topilmadi!\n"
            "Railway Variables bo'limiga "
            "BOT_TOKEN qo'shing."
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

    # Video linklari
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            download_video
        )
    )

    logging.info(
        "=============================="
    )

    logging.info(
        "BOT ISHLAYAPTI"
    )

    logging.info(
        "=============================="
    )

    app.run_polling()


# ==========================================
# START
# ==========================================

if __name__ == "__main__":
    main()
