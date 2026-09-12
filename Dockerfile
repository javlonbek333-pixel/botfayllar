# 1. Asosiy Python obrazini tanlaymiz
FROM python:3.10-slim

# 2. Serverga FFmpeg va zarur tizim paketlarini o'rnatamiz
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 3. Ishchi papkani belgilaymiz
WORKDIR /app

# 4. requirements.txt faylini nusxalaymiz va kutubxonalarni o'rnatamiz
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# 5. Loyihaning barcha fayllarini nusxalaymiz
COPY . .

# 6. Botingizni ishga tushirish buyrug'i
CMD ["python", "main.py"]
