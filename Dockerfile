FROM python:3.12-slim

# Kerakli tizim paketlari
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    ca-certificates \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno o'rnatish
RUN curl -fsSL https://deno.land/install.sh | sh

# Deno PATH
ENV DENO_INSTALL=/root/.deno
ENV PATH="/root/.deno/bin:${PATH}"

# Ishchi papka
WORKDIR /app

# Python kutubxonalarini o'rnatish
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Bot kodi
COPY main.py .

# Botni ishga tushirish
CMD ["python", "main.py"]
