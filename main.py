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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ChatAction
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/tmp/videola"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

TARGET_MB_PER_MINUTE = 2.5
# Telegram Bot API odatiy serverida video yuborish limiti 50 MB.
# 49 MiB qilib qo'yamiz, shunda 50 MB chegarasiga urilmaydi.
TELEGRAM_MAX_VIDEO_BYTES = 48_000_000
AUDIO_KBPS = 64
MIN_VIDEO_KBPS = 160
MAX_VIDEO_KBPS = 2500
CAPTION = "@yuklatgbot orqali yuklab olindi"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO)
logger = logging.getLogger("videola_bot")


def run_cmd(cmd, timeout=600):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)


def ffprobe_duration(path):
    r = run_cmd(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)], 60)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def ffprobe_dimensions(path):
    r = run_cmd(["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)], 60)
    m = re.match(r"(\d+)x(\d+)", r.stdout.strip())
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def has_audio(path):
    r = run_cmd(["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=index", "-of", "csv=p=0", str(path)], 60)
    return bool(r.stdout.strip())


def find_downloaded_file(folder):
    files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() not in {".part", ".ytdl", ".json", ".jpg", ".jpeg", ".png", ".webp"}]
    return max(files, key=lambda p: p.stat().st_size) if files else None


def safe_name(value, limit=70):
    value = re.sub(r"[^\w\-. ]+", "", value, flags=re.UNICODE).strip()
    return (value or "video")[:limit]


def is_url(text):
    return bool(re.match(r"^https?://", text.strip(), re.I))


def is_supported_url(url):
    hosts = ("youtube.com", "youtu.be", "instagram.com", "tiktok.com", "facebook.com", "fb.watch",
             "ok.ru", "odnoklassniki.ru", "pinterest.com", "snapchat.com", "likee.video", "threads.net")
    low = url.lower()
    return any(h in low for h in hosts)


def yt_opts(folder):
    opts = {
        "outtmpl": str(folder / "%(id)s.%(ext)s"),
        "noplaylist": True, "quiet": True, "no_warnings": True,
        "retries": 3, "fragment_retries": 3, "concurrent_fragment_downloads": 4,
        "merge_output_format": "mp4", "format": "bv*+ba/b",
    }
    b64 = os.getenv("YOUTUBE_COOKIES_B64", "").strip()
    cookie_file = os.getenv("YOUTUBE_COOKIE_FILE", "").strip()
    if b64:
        try:
            p = folder / "youtube_cookies.txt"
            p.write_bytes(base64.b64decode(b64))
            opts["cookiefile"] = str(p)
        except Exception:
            logger.exception("Cookie decode xatosi")
    elif cookie_file and Path(cookie_file).exists():
        opts["cookiefile"] = cookie_file
    return opts


def download_url(url, folder):
    with yt_dlp.YoutubeDL(yt_opts(folder)) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title") or "video"
        p = Path(ydl.prepare_filename(info))
        if not p.exists():
            p = find_downloaded_file(folder)
        return p, title


def compress_video(source, output):
    duration = ffprobe_duration(source)
    if duration <= 0:
        raise RuntimeError("Video davomiyligi aniqlanmadi.")
    width, height = ffprobe_dimensions(source)
    if not width or not height:
        raise RuntimeError("Video o'lchami aniqlanmadi.")

    target_bytes = max(300_000, duration / 60.0 * TARGET_MB_PER_MINUTE * 1_000_000)
    # 14 MB sun'iy cheklovi yo'q. Faqat Telegram upload uchun xavfsiz 48 MB.
    target_bytes = min(target_bytes, int(TELEGRAM_MAX_VIDEO_BYTES * 0.96))
    audio_exists = has_audio(source)
    total_kbps = int(target_bytes * 8 / duration / 1000)
    video_kbps = total_kbps - AUDIO_KBPS if audio_exists else total_kbps
    video_kbps = max(MIN_VIDEO_KBPS, min(MAX_VIDEO_KBPS, video_kbps))

    cmd = [
        "ffmpeg", "-y", "-i", str(source),
        "-map", "0:v:0",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-b:v", f"{video_kbps}k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]

    # Audio bo'lmasa -b:a berilmaydi. Shu screenshotdagi FFmpeg warningni tuzatadi.
    if audio_exists:
        cmd += [
            "-map", "0:a:0",
            "-c:a", "aac",
            "-b:a", f"{AUDIO_KBPS}k",
            "-ac", "2",
        ]
    else:
        cmd += ["-an"]

    cmd += ["-f", "mp4", str(output)]
    r = run_cmd(cmd, max(600, int(duration * 10)))
    if r.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(r.stderr[-2500:])

    if ffprobe_dimensions(output) != (width, height):
        raise RuntimeError(f"Resolution o'zgardi: original={width}x{height}, output={ffprobe_dimensions(output)}")
    return duration, width, height


def extract_shazam_audio(source: Path, output: Path):
    r = run_cmd([
        "ffmpeg", "-y", "-i", str(source),
        "-map", "0:a:0", "-vn",
        "-t", "45",
        "-ac", "1", "-ar", "44100",
        "-c:a", "mp3", "-b:a", "128k",
        str(output),
    ], 300)
    if r.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(r.stderr[-2000:])


def extract_mp3(source, output):
    r = run_cmd(["ffmpeg", "-y", "-i", str(source), "-map", "0:a:0", "-vn",
                 "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", str(output)], 600)
    if r.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(r.stderr[-2500:])


async def shazam_track(source: Path):
    try:
        result = await Shazam().recognize(str(source))
        track = result.get("track") or {}
        title = track.get("title")
        artist = track.get("subtitle") or track.get("artist")
        if title:
            return artist or "Noma'lum", title
    except Exception:
        logger.exception("Shazam recognize xatosi")
    return None, None


def google_search(query, limit=10):
    key, cx = os.getenv("GOOGLE_API_KEY", "").strip(), os.getenv("GOOGLE_CX", "").strip()
    if not key or not cx:
        return []
    try:
        r = requests.get("https://www.googleapis.com/customsearch/v1",
                         params={"key": key, "cx": cx, "q": query, "num": min(limit, 10)}, timeout=15)
        r.raise_for_status()
        return [(x.get("title", "Natija"), x.get("link", "")) for x in r.json().get("items", []) if x.get("link")]
    except Exception:
        logger.exception("Google qidiruv xatosi")
        return []


async def start(update, context):
    await update.message.reply_text(
        "👋 Salom!\nMen sizga musiqa topishga yordam beraman 🎶\n\n"
        "🎵 Qo'shiq yoki ijrochi nomi\n🔤 Qo'shiq matni\n🎙 Musiqa bilan ovozli xabar\n"
        "📹 Musiqa bilan video\n🔊 Audioyozuv\n🎥 Musiqa bilan videoxabar\n"
        "🔗 Instagram, TikTok, YouTube va boshqa saytlar havolasi\n\n🕺 Rohatlaning!"
    )


async def cleanup_job(context, job_id, delay=1800):
    await asyncio.sleep(delay)
    job = context.bot_data.setdefault("media_jobs", {}).pop(job_id, None)
    if job:
        shutil.rmtree(job["dir"], ignore_errors=True)


async def process_url(update, context, url):
    message = update.message
    work = DOWNLOAD_DIR / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    try:
        await message.reply_text("⏳ Yuklanmoqda...")
        downloaded, title = await asyncio.to_thread(download_url, url, work)
        if not downloaded or not downloaded.exists():
            raise RuntimeError("Video fayli topilmadi.")

        original = work / f"original{downloaded.suffix.lower()}"
        shutil.copy2(downloaded, original)

        artist = song = None
        if has_audio(original):
            shazam_audio = work / "shazam.mp3"
            try:
                await asyncio.to_thread(extract_shazam_audio, original, shazam_audio)
                artist, song = await shazam_track(shazam_audio)
            except Exception:
                logger.exception("URL Shazam xatosi")

        encoded = work / "video_final.mp4"
        await asyncio.to_thread(compress_video, downloaded, encoded)

        if encoded.stat().st_size > TELEGRAM_MAX_VIDEO_BYTES:
            raise RuntimeError("Tayyor video Telegramning 50 MB limitidan katta.")

        job_id = uuid.uuid4().hex[:16]
        context.bot_data.setdefault("media_jobs", {})[job_id] = {
            "audio_source": str(original), "dir": str(work),
            "artist": artist, "song": song,
        }
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎵 MP3 yuklash", callback_data=f"jobmp3|{job_id}")
        ]]) if has_audio(original) else None

        caption = (f"🎵 {artist} — {song}\n\n{CAPTION}" if artist and song else CAPTION)

        await message.chat.send_action(ChatAction.UPLOAD_VIDEO)
        with open(encoded, "rb") as video_fh:
            await message.reply_video(
                video=InputFile(video_fh, filename=f"{safe_name(title)}.mp4"),
                caption=caption,
                supports_streaming=True,
                reply_markup=keyboard,
            )
        asyncio.create_task(cleanup_job(context, job_id))
    except Exception as e:
        logger.exception("URL processing xatosi")
        await message.reply_text(f"❌ Videoni yuklashda xatolik:\n{str(e)[:1200]}")
        shutil.rmtree(work, ignore_errors=True)



def youtube_song_search(query, limit=10):
    folder = DOWNLOAD_DIR / ("search_" + uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        opts = yt_opts(folder)
        opts.update({"skip_download": True, "extract_flat": True, "quiet": True, "no_warnings": True})
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        results = []
        for item in (info.get("entries") or []):
            if not item:
                continue
            url = item.get("webpage_url") or item.get("url")
            title = item.get("title") or "Noma'lum"
            if url:
                results.append({"url": url, "title": title})
        return results
    finally:
        shutil.rmtree(folder, ignore_errors=True)


async def send_song_search_results(update, context, text):
    await update.message.reply_text("🔎 YouTube'dan qo'shiq qidirilmoqda...")
    results = await asyncio.to_thread(youtube_song_search, text, 10)
    if not results:
        await update.message.reply_text("❌ Qo'shiq topilmadi.")
        return
    jobs = context.bot_data.setdefault("song_search_jobs", {})
    buttons = []
    for item in results:
        job_id = uuid.uuid4().hex[:12]
        jobs[job_id] = item
        buttons.append([InlineKeyboardButton(f"🎵 {item['title'][:55]}", callback_data=f"searchmp3|{job_id}")])
    await update.message.reply_text(
        "🎵 Topilgan qo'shiqlar:\nKeraklisini tanlang:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def search_song_mp3(update, context):
    q = update.callback_query
    await q.answer("⏳ MP3 yuklanmoqda...")
    job_id = (q.data or "").split("|", 1)[-1]
    job = context.bot_data.setdefault("song_search_jobs", {}).get(job_id)
    if not job:
        await q.message.reply_text("❌ Qidiruv sessiyasi tugagan. Qo'shiq nomini qaytadan yuboring.")
        return
    work = DOWNLOAD_DIR / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    try:
        opts = yt_opts(work)
        opts["format"] = "bestaudio/best"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, job["url"], download=True)
            downloaded = Path(ydl.prepare_filename(info))
        if not downloaded.exists():
            downloaded = find_downloaded_file(work)
        if not downloaded:
            raise RuntimeError("Audio fayl topilmadi.")
        output = work / "song.mp3"
        await asyncio.to_thread(extract_mp3, downloaded, output)
        with open(output, "rb") as fh:
            await q.message.reply_audio(
                audio=InputFile(fh, filename=f"{safe_name(job['title'])}.mp3"),
                caption=CAPTION,
            )
    except Exception as e:
        logger.exception("Qidirilgan qo'shiq MP3 xatosi")
        await q.message.reply_text(f"❌ MP3 yuklashda xatolik:\n{str(e)[:1500]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

async def url_message(update, context):
    text = (update.message.text or "").strip()
    if is_url(text):
        if is_supported_url(text):
            if "youtube.com" in text.lower() or "youtu.be" in text.lower():
                context.user_data["pending_youtube"] = text
                await update.message.reply_text("Qaysi format kerak?", reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🎬 VIDEO", callback_data="ytvideo"),
                    InlineKeyboardButton("🎵 MP3", callback_data="ytmp3"),
                ]]))
            else:
                await process_url(update, context, text)
        else:
            await update.message.reply_text("🔎 Bu havola qo'llab-quvvatlanadigan saytlar ro'yxatida yo'q.")
        return

    # Qo'shiqchi yoki qo'shiq nomi: Google API shart emas, YouTube orqali qidiriladi.
    if len(text) >= 2:
        try:
            await send_song_search_results(update, context, text)
        except Exception as e:
            logger.exception("YouTube qo'shiq qidiruv xatosi")
            await update.message.reply_text(f"❌ Qo'shiq qidirishda xatolik:\n{str(e)[:1500]}")
    else:
        await update.message.reply_text("🎵 Qo'shiq yoki ijrochi nomini yuboring.")


async def youtube_download(update, context, mode):
    q = update.callback_query
    await q.answer()
    url = context.user_data.get("pending_youtube")
    if not url:
        await q.message.reply_text("❌ Havola topilmadi. Qaytadan yuboring.")
        return

    work = DOWNLOAD_DIR / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    try:
        await q.message.reply_text("⏳ Yuklanmoqda...")
        downloaded, title = await asyncio.to_thread(download_url, url, work)
        if not downloaded or not downloaded.exists():
            raise RuntimeError("Yuklangan fayl topilmadi.")

        if mode == "mp3":
            output = work / "audio.mp3"
            await asyncio.to_thread(extract_mp3, downloaded, output)
            await q.message.reply_audio(audio=InputFile(open(output, "rb"), filename=f"{safe_name(title)}.mp3"), caption=CAPTION)
            shutil.rmtree(work, ignore_errors=True)
            return

        original = work / f"original{downloaded.suffix.lower()}"
        shutil.copy2(downloaded, original)

        artist = song = None
        if has_audio(original):
            shazam_audio = work / "shazam.mp3"
            try:
                await asyncio.to_thread(extract_shazam_audio, original, shazam_audio)
                artist, song = await shazam_track(shazam_audio)
            except Exception:
                logger.exception("YouTube Shazam xatosi")

        encoded = work / "video_final.mp4"
        await asyncio.to_thread(compress_video, downloaded, encoded)

        if encoded.stat().st_size > TELEGRAM_MAX_VIDEO_BYTES:
            raise RuntimeError("Tayyor video Telegramning 50 MB limitidan katta.")

        job_id = uuid.uuid4().hex[:16]
        context.bot_data.setdefault("media_jobs", {})[job_id] = {
            "audio_source": str(original), "dir": str(work),
            "artist": artist, "song": song,
        }
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎵 MP3 yuklash", callback_data=f"jobmp3|{job_id}")
        ]]) if has_audio(original) else None

        caption = (f"🎵 {artist} — {song}\n\n{CAPTION}" if artist and song else CAPTION)
        with open(encoded, "rb") as video_fh:
            await q.message.reply_video(
                video=InputFile(video_fh, filename=f"{safe_name(title)}.mp4"),
                caption=caption,
                supports_streaming=True,
                reply_markup=keyboard,
            )
        asyncio.create_task(cleanup_job(context, job_id))
    except Exception as e:
        logger.exception("YouTube xatosi")
        await q.message.reply_text(f"❌ YouTube yuklashda xatolik:\n{str(e)[:1200]}")
        shutil.rmtree(work, ignore_errors=True)


async def media_callback(update, context):
    q = update.callback_query
    data = q.data or ""
    if data == "ytvideo":
        await youtube_download(update, context, "video")
    elif data == "ytmp3":
        await youtube_download(update, context, "mp3")
    elif data.startswith("jobmp3|"):
        await q.answer("⏳ MP3 tayyorlanmoqda...")
        job = context.bot_data.setdefault("media_jobs", {}).get(data.split("|", 1)[1])
        if not job:
            await q.message.reply_text("❌ Bu video sessiyasi tugagan. Videoni qaytadan yuboring.")
            return
        source, work = Path(job["audio_source"]), Path(job["dir"])
        try:
            if not source.exists():
                raise RuntimeError("Original audio topilmadi.")
            output = work / "extracted.mp3"
            await asyncio.to_thread(extract_mp3, source, output)
            artist = job.get("artist")
            song = job.get("song")
            filename = f"{safe_name(artist + ' - ' + song)}.mp3" if artist and song else "audio.mp3"
            with open(output, "rb") as audio_fh:
                await q.message.reply_audio(audio=InputFile(audio_fh, filename=filename), caption=CAPTION)
        except Exception as e:
            logger.exception("MP3 callback xatosi")
            await q.message.reply_text(f"❌ MP3 tayyorlashda xatolik:\n{str(e)[:1000]}")


async def recognize_audio(update, context):
    message = update.message
    work = DOWNLOAD_DIR / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    try:
        await message.reply_text("🎧 Musiqa aniqlanmoqda...")
        source = work / "input"
        if message.voice:
            f = await message.voice.get_file(); await f.download_to_drive(str(source) + ".ogg"); source = Path(str(source) + ".ogg")
        elif message.audio:
            f = await message.audio.get_file(); await f.download_to_drive(str(source) + ".mp3"); source = Path(str(source) + ".mp3")
        elif message.video:
            f = await message.video.get_file(); await f.download_to_drive(str(source) + ".mp4"); source = Path(str(source) + ".mp4")
        elif message.video_note:
            f = await message.video_note.get_file(); await f.download_to_drive(str(source) + ".mp4"); source = Path(str(source) + ".mp4")
        else:
            await message.reply_text("❌ Audio yoki video topilmadi."); return

        shazam_source = source
        if message.video or message.video_note:
            shazam_source = work / "shazam.mp3"
            await asyncio.to_thread(extract_shazam_audio, source, shazam_source)

        artist, title = await shazam_track(shazam_source)
        if not title:
            await message.reply_text("❌ Qo'shiq aniqlanmadi."); return

        artist_name = artist or "Noma'lum"
        context.user_data["song_search"] = f"{artist_name} {title}".strip()
        await message.reply_text(
            f"🎵 Qo'shiq topildi!\n\n👤 Artist: {artist_name}\n🎶 Qo'shiq: {title}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎵 MP3", callback_data="songmp3")
            ]])
        )
    except Exception:
        logger.exception("Shazam xatosi")
        await message.reply_text("❌ Qo'shiqni aniqlab bo'lmadi.")
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def song_callback(update, context):
    q = update.callback_query
    await q.answer("⏳ Yuklanmoqda...")
    search = context.user_data.get("song_search")
    if not search:
        await q.message.reply_text("❌ Qo'shiq ma'lumoti topilmadi."); return
    work = DOWNLOAD_DIR / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    try:
        opts = yt_opts(work); opts["format"] = "bestaudio/best"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, f"ytsearch1:{search}", True)
            downloaded = Path(ydl.prepare_filename(info["entries"][0]))
        if not downloaded.exists():
            downloaded = find_downloaded_file(work)
        output = work / "song.mp3"
        await asyncio.to_thread(extract_mp3, downloaded, output)
        await q.message.reply_audio(audio=InputFile(open(output, "rb"), filename="song.mp3"), caption=CAPTION)
    except Exception:
        logger.exception("Song download xatosi")
        await q.message.reply_text("❌ Qo'shiqni yuklab bo'lmadi.")
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def error_handler(update, context):
    logger.exception("Unhandled exception", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN topilmadi. Railway Variables ichiga BOT_TOKEN qo'ying.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(media_callback, pattern=r"^(ytvideo|ytmp3|jobmp3\|.+)$"))
    app.add_handler(CallbackQueryHandler(song_callback, pattern=r"^songmp3$"))
    app.add_handler(CallbackQueryHandler(search_song_mp3, pattern=r"^searchmp3\|.+$"))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE, recognize_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, url_message))
    app.add_error_handler(error_handler)
    logger.info("Bot ishga tushdi")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
