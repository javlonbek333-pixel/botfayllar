FROM node:22-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 POT_PROVIDER_URL=http://127.0.0.1:4416
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip ffmpeg git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip3 install --break-system-packages -r requirements.txt
RUN git clone --single-branch --branch 2.0.0 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil && cd /opt/bgutil/server && npm ci && npx tsc
COPY main.py .
CMD ["bash","-lc","node /opt/bgutil/server/build/main.js --host 127.0.0.1 --port 4416 & exec python3 /app/main.py"]
