# Yengil va tez ishlaydigan Python tasviri
FROM python:3.11-slim

# Tizim paketlarini yangilash va FFmpeg o'rnatish (video/audio qayta ishlash uchun)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Ishchi katalogni belgilash
WORKDIR /app

# Kutubxona faylini nusxalash va o'rnatish
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Loyiha fayllarini nusxalash
COPY . .

# Vaqtinchalik fayllar uchun papka yaratish
RUN mkdir -p /tmp/bot_downloads

# Botni ishga tushirish
CMD ["python", "bot.py"]
