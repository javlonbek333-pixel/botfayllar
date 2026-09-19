import os
import subprocess
import yt_dlp

def compress_video(input_path: str, output_path: str) -> bool:
    """
    Video o'lchamini o'zgartirmasdan, FFmpeg CRF parametri orqali
    sifatini pasaytirib, hajmini qisqartiradi.
    """
    try:
        command = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vcodec", "libx264",
            "-crf", "32",
            "-preset", "faster",
            "-acodec", "aac",
            "-b:a", "128k",
            output_path
        ]
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception as e:
        print(f"Video siqishda xatolik: {e}")
        return False

def download_video_from_url(url: str, output_path: str) -> bool:
    """yt-dlp orqali Instagram, TikTok, YouTube va boshqa platformalardan video yuklaydi."""
    ydl_opts = {
        'outtmpl': output_path,
        'format': 'mp4/best',
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        print(f"Videoni yuklashda xatolik: {e}")
        return False

def download_audio_by_title(query_text: str, output_audio_path: str) -> bool:
    """Nomi va ijrochisi bo'yicha YouTube'dan audio qidiradi va yuklaydi."""
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_audio_path.replace('.mp3', ''),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'default_search': 'ytsearch1:',
        'quiet': True,
        'no_warnings': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([query_text])
        return True
    except Exception as e:
        print(f"Audioni yuklashda xatolik: {e}")
        return False
