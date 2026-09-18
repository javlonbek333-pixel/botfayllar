FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ENV DENO_INSTALL=/root/.deno
ENV PATH="/root/.deno/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

ENV FFMPEG_LOCATION=/usr/bin

WORKDIR /app

# FFmpeg + Node.js + Deno uchun kerakli paketlar
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Deno
RUN curl -fsSL https://deno.land/install.sh | sh

# Python dependencies
COPY requirements.txt .

RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------
# BGUTIL PO TOKEN PROVIDER
# ---------------------------------------------------------

RUN git clone \
    --depth 1 \
    --branch 1.3.1 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    /opt/bgutil

WORKDIR /opt/bgutil/server

RUN npm ci --omit=dev --no-audit --no-fund && \
    npm ci --no-audit --no-fund && \
    npx tsc

WORKDIR /app

COPY main.py .

# Provider + Telegram bot birga ishga tushadi
CMD ["sh", "-c", "node /opt/bgutil/server/build/main.js --host 127.0.0.1 --port 4416 & sleep 3 && python main.py"]
