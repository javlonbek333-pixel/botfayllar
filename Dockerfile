FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y \
    ffmpeg \
    curl \
    unzip \
    ca-certificates \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# YouTube JavaScript challenge uchun Deno
RUN curl -fsSL https://deno.land/install.sh | sh

ENV PATH="/root/.deno/bin:${PATH}"

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY main.py .

CMD ["python", "main.py"]
