FROM python:3.10-slim

# Tizim paketlarini o'rnatish (unzip, 7z va ffmpeg xatoliklarini bartaraf etadi)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    unzip \
    p7zip-full \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Kutubxonalarni o'rnatish
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Loyiha fayllarini ko'chirish
COPY . .

# Botni ishga tushirish
CMD ["python", "main.py"]
