FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Deno
ENV DENO_INSTALL=/root/.deno
ENV PATH="/root/.deno/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# FFmpeg + FFprobe
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        unzip \
    && which ffmpeg \
    && which ffprobe \
    && ffmpeg -version \
    && ffprobe -version \
    && rm -rf /var/lib/apt/lists/*

# Deno
RUN curl -fsSL https://deno.land/install.sh | sh

WORKDIR /app

COPY requirements.txt .

RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir -r requirements.txt

COPY main.py .

# FFmpeg joylashuvini aniq ko'rsatamiz
ENV FFMPEG_LOCATION=/usr/bin

CMD ["python", "main.py"]
