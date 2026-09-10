FROM python:3.12-slim

# FFmpeg va FFprobe
RUN apt-get update && \
    apt-get install -y \
    ffmpeg \
    curl \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]
