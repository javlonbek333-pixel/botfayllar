FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ENV DENO_INSTALL=/root/.deno
ENV PATH="/root/.deno/bin:$PATH"

RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    ca-certificates \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno
RUN curl -fsSL https://deno.land/install.sh | sh

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -U "yt-dlp[default]" && \
    pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]
