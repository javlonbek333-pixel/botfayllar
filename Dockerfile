FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Deno — yt-dlp YouTube JS challenge uchun
ENV DENO_INSTALL=/root/.deno
ENV PATH="/root/.deno/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# FFmpeg
ENV FFMPEG_LOCATION=/usr/bin

WORKDIR /app

# FFmpeg, Deno va kerakli tizim paketlari
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Deno o‘rnatish
RUN curl -fsSL https://deno.land/install.sh | sh

# Python kutubxonalarini o‘rnatish
COPY requirements.txt .

RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir -r requirements.txt

# Bot kodi
COPY main.py .

# Railway botni ishga tushiradi
CMD ["python", "main.py"]
