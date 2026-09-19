FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# FFmpeg + Node.js
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    npm \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python kutubxonalari
COPY requirements.txt .

RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir -r requirements.txt

# Bot
COPY main.py .

CMD ["python", "main.py"]
