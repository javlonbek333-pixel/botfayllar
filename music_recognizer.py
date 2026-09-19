import os
import requests
import subprocess
from config import AUDD_API_KEY

def extract_audio(video_path: str, output_audio_path: str) -> bool:
    """FFmpeg orqali videodan MP3 audio ajratib oladi."""
    try:
        command = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "libmp3lame",
            "-q:a", "4",
            output_audio_path
        ]
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception as e:
        print(f"Audio ajratishda xatolik: {e}")
        return False

def recognize_music(audio_path: str) -> dict | None:
    """AudD API orqali audio fayldan musiqani aniqlaydi."""
    if not os.path.exists(audio_path):
        return None

    data = {
        'api_token': AUDD_API_KEY,
        'return': 'apple_music,spotify',
    }

    try:
        with open(audio_path, 'rb') as f:
            files = {'file': f}
            response = requests.post('https://api.audd.io/', data=data, files=files, timeout=30)
            result = response.json()
            
        if result.get('status') == 'success' and result.get('result'):
            return result['result']
    except Exception as e:
        print(f"Musiqa aniqlashda xatolik: {e}")
        
    return None
