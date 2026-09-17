FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y \
    ffmpeg \
    curl \
    ca-certificates \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Agar cookies.txt GitHub'da mavjud bo'lmasa ham
# bot ishlayveradi.
# Railway'da alohida xavfsiz fayl berilsa,
# /app/cookies.txt dan foydalanadi.

CMD ["python", "main.py"]
