import logging
import os
import re
import shutil
import subprocess
import uuid

# FFmpeg ni Railway muhitiga ulash
import static_ffmpeg
import yt_dlp
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# FFmpeg va FFprobe fayllarini majburiy ro'yxatdan o'tkazish
import static_ffmpeg

static_ffmpeg.add_paths()

ffmpeg_exe = "ffmpeg"
ffprobe_exe = "ffprobe"

# ==========================================
# SOZLAMALAR
# ==========================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DOWNLOAD_DIR = "/tmp/videos"
TARGET_SIZE_MB = 45

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ==========================================
# LOG
# ==========================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)


# ==========================================
# /START
# ==========================================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  await update.message.reply_text(
      "👋 Assalomu alaykum!\n\n"
      "🎬 Men YouTube va Instagram videolarini yuklab beraman.\n\n"
      "🔗 Video havolasini yuboring."
  )


# ==========================================
# FAYL HAJMI MEGAPAYTDA
# ==========================================


def get_size_mb(filename):
  if not os.path.exists(filename):
    return 0
  return os.path.getsize(filename) / (1024 * 1024)


# ==========================================
# VIDEO DAVOMIYLIGI (SEKUND)
# ==========================================


def get_duration(filename):
  command = [
      ffprobe_exe,
      "-v",
      "error",
      "-show_entries",
      "format=duration",
      "-of",
      "default=noprint_wrappers=1:nokey=1",
      filename,
  ]

  result = subprocess.run(
      command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
  )

  try:
    return float(result.stdout.strip())
  except Exception:
    return 60


# ==========================================
# VIDEO SIQISH (FFMPEG)
# ==========================================


def compress_video(input_file, output_file):
  duration = get_duration(input_file)
  if duration <= 0:
    duration = 60

  # Maqsadli video bitrate hisoblash
  target_bits = TARGET_SIZE_MB * 8 * 1024 * 1024
  audio_bitrate = 96000
  video_bitrate = int((target_bits / duration) - audio_bitrate)

  if video_bitrate < 150000:
    video_bitrate = 150000
  if video_bitrate > 3500000:
    video_bitrate = 3500000

  command = [
      ffmpeg_exe,
      "-y",
      "-i",
      input_file,
      # Video kadrlarini 480p ga tushirish (Hajmni keskin kamaytiradi)
      "-vf",
      "scale=-2:480",
      "-c:v",
      "libx264",
      "-b:v",
      str(video_bitrate),
      "-maxrate",
      str(int(video_bitrate * 1.15)),
      "-bufsize",
      str(int(video_bitrate * 2)),
      "-preset",
      "veryfast",
      "-c:a",
      "aac",
      "-b:a",
      "96k",
      "-movflags",
      "+faststart",
      output_file,
  ]

  logging.info("FFmpeg siqish ishga tushmoqda...")

  result = subprocess.run(
      command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
  )

  if result.returncode != 0:
    logging.error("FFMPEG XATOSI:\n%s", result.stderr)
    raise Exception("FFmpeg xatosi:\n" + result.stderr[-1000:])

  if not os.path.exists(output_file):
    raise Exception("FFmpeg video fayl yaratmadi.")


# ==========================================
# YOUTUBE / INSTAGRAM TEKSHIRISH
# ==========================================


def is_supported_url(url):
  supported_domains = [
      "youtube.com",
      "youtu.be",
      "youtube-nocookie.com",
      "instagram.com",
  ]
  url_lower = url.lower()
  return any(domain in url_lower for domain in supported_domains)


# ==========================================
# VIDEO YUKLASH VA YUBORISH
# ==========================================


async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if not update.message or not update.message.text:
    return

  url = update.message.text.strip()

  if not re.match(r"^https?://", url) or not is_supported_url(url):
    await update.message.reply_text(
        "❌ Noto'g'ri havola. Faqat YouTube va Instagram havolalarini yuboring."
    )
    return

  status = await update.message.reply_text("⏳ Video tayyorlanmoqda...")
  job_id = str(uuid.uuid4())
  work_dir = os.path.join(DOWNLOAD_DIR, job_id)
  os.makedirs(work_dir, exist_ok=True)

  output_file = os.path.join(work_dir, "compressed.mp4")

  try:
    await status.edit_text("⬇️ Video yuklanmoqda...")

    ydl_opts = {
        "format": (
            "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best"
        ),
        "outtmpl": os.path.join(work_dir, "original.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": ffmpeg_exe,
        "extractor_args": {"youtube": {"player_client": ["android", "ios"]}},
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
      info = ydl.extract_info(url, download=True)
      prepared_file = ydl.prepare_filename(info)

    possible_files = [
        f
        for f in os.listdir(work_dir)
        if os.path.isfile(os.path.join(work_dir, f)) and f != "compressed.mp4"
    ]
    if not possible_files:
      raise Exception("Yuklangan video fayli topilmadi.")

    input_file = os.path.join(
        work_dir,
        max(
            possible_files,
            key=lambda x: os.path.getsize(os.path.join(work_dir, x)),
        ),
    )
    original_size = get_size_mb(input_file)

    await status.edit_text(
        f"✅ Video yuklandi ({original_size:.1f} MB).\n🔄 Hajmi"
        " kamaytirilmoqda..."
    )

    # Siqish
    compress_video(input_file, output_file)
    compressed_size = get_size_mb(output_file)

    final_file = (
        output_file
        if (compressed_size > 0 and os.path.exists(output_file))
        else input_file
    )
    final_size = compressed_size if final_file == output_file else original_size

    await status.edit_text("📤 Video Telegramga yuborilmoqda...")

    with open(final_file, "rb") as video:
      await update.message.reply_video(
          video=video,
          caption=(
              f"🎬 Video tayyor!\n📦 Hajmi: {final_size:.1f} MB\n✅ Siqildi"
          ),
          supports_streaming=True,
      )

    await status.delete()

  except Exception as e:
    logging.exception("BOT XATOSI")
    error_text = str(e)[-1000:]
    try:
      await status.edit_text(f"❌ Xatolik yuz berdi:\n\n{error_text}")
    except Exception:
      pass

  finally:
    if os.path.exists(work_dir):
      shutil.rmtree(work_dir, ignore_errors=True)


# ==========================================
# MAIN
# ==========================================


def main():
  if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi! Railway Variables ga qo'shing.")

  app = ApplicationBuilder().token(BOT_TOKEN).build()
  app.add_handler(CommandHandler("start", start))
  app.add_handler(
      MessageHandler(filters.TEXT & ~filters.COMMAND, download_video)
  )

  logging.info("BOT ISHLAYAPTI")
  app.run_polling()


if __name__ == "__main__":
  main()
