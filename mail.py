import static_ffmpeg
static_ffmpeg.add_paths()  # FFmpeg'ni tizimga ulash
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
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

DOWNLOAD_DIR = "/tmp/videos"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def compress_video(input_file, output_file):
    """
    Videoni MP4 H.264 formatida siqadi.
    Sifatni imkon qadar saqlaydi.
    """

    command = [
        "ffmpeg",
        "-y",
        "-i", input_file,

        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "28",

        "-c:a", "aac",
        "-b:a", "128k",

        "-movflags", "+faststart",

        output_file
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        logging.error(result.stderr)
        raise Exception("FFmpeg xatosi")


def get_size_mb(filename):
    return os.path.getsize(filename) / (1024 * 1024)


async def download_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message or not update.message.text:
        return

    url = update.message.text.strip()

    # Faqat URL bo'lsa ishlaydi
    if not re.match(r"^https?://", url):
        await update.message.reply_text(
            "❌ Iltimos, YouTube yoki Instagram video havolasini yuboring."
        )
        return

    status = await update.message.reply_text(
        "⏳ Video yuklanmoqda..."
    )

    job_id = str(uuid.uuid4())

    original = os.path.join(
        DOWNLOAD_DIR,
        f"{job_id}_original"
    )

    compressed = os.path.join(
        DOWNLOAD_DIR,
        f"{job_id}_compressed.mp4"
    )

    try:

        # YouTube / Instagram yuklash
        ydl_opts = {
            # Telegram uchun MP4 formatni afzal ko'ramiz
            "format": (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "best[ext=mp4]/best"
            ),

            "outtmpl": original + ".%(ext)s",

            "merge_output_format": "mp4",

            "noplaylist": True,

            "quiet": True,

            "no_warnings": True,

            # YouTube o'zgarishlariga mos
            "retries": 3,

            "fragment_retries": 3,

            "socket_timeout": 30,

            "http_chunk_size": 10485760,
        }

        await status.edit_text(
            "⬇️ Video yuklanmoqda..."
        )

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

            downloaded_file = ydl.prepare_filename(info)

        # yt-dlp MP4 merge qilgan bo'lishi mumkin
        if os.path.exists(downloaded_file):
            input_file = downloaded_file

        elif os.path.exists(downloaded_file.rsplit(".", 1)[0] + ".mp4"):
            input_file = downloaded_file.rsplit(".", 1)[0] + ".mp4"

        else:
            # Papkadan shu job bilan boshlanadigan faylni topamiz
            files = os.listdir(DOWNLOAD_DIR)

            candidates = [
                os.path.join(DOWNLOAD_DIR, f)
                for f in files
                if f.startswith(job_id)
            ]

            if not candidates:
                raise Exception(
                    "Yuklangan video fayli topilmadi"
                )

            input_file = candidates[0]

        original_size = get_size_mb(input_file)

        await status.edit_text(
            f"✅ Yuklandi: {original_size:.1f} MB\n"
            f"🔄 Video siqilmoqda..."
        )

        # Video siqish
        compress_video(
            input_file,
            compressed
        )

        compressed_size = get_size_mb(compressed)

        # Agar siqilgan fayl aslidan katta bo'lsa,
        # asl faylni yuboramiz
        if compressed_size >= original_size:
            final_file = input_file
            final_size = original_size
        else:
            final_file = compressed
            final_size = compressed_size

        await status.edit_text(
            f"📤 Telegramga yuborilmoqda...\n"
            f"📦 Hajmi: {final_size:.1f} MB"
        )

        with open(final_file, "rb") as video:

            await update.message.reply_video(
                video=video,
                caption=(
                    f"🎬 Video tayyor\n"
                    f"📦 Hajmi: {final_size:.1f} MB"
                ),

                supports_streaming=True
            )

        await status.delete()

    except Exception as e:

        logging.exception(
            "Video yuklash xatosi"
        )

        try:
            await status.edit_text(
                "❌ Videoni yuklash yoki siqishda xatolik yuz berdi.\n\n"
                f"Xatolik: {str(e)[:500]}"
            )
        except Exception:
            pass

    finally:

        # Ish tugagach barcha vaqtinchalik fayllarni o'chirish
        try:
            for filename in os.listdir(DOWNLOAD_DIR):

                if filename.startswith(job_id):

                    path = os.path.join(
                        DOWNLOAD_DIR,
                        filename
                    )

                    if os.path.isfile(path):
                        os.remove(path)

        except Exception:
            pass


def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN Railway Variables ichida ko'rsatilmagan!"
        )

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            download_video
        )
    )

    logging.info("BOT ISHLAYAPTI")

    app.run_polling()


if __name__ == "__main__":
    main()
