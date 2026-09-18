FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ENV DENO_INSTALL=/root/.deno
ENV PATH="/root/.deno/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

ENV FFMPEG_LOCATION=/usr/bin

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    unzip \
    nodejs \
    npm && \
    rm -rf /var/lib/apt/lists/*

# YouTube EJS uchun Deno
RUN curl -fsSL https://deno.land/install.sh | sh

COPY requirements.txt .

RUN python -m pip install --upgrade pip && \
    python -m pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]
