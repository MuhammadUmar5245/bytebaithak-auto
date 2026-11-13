FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Karachi \
    APP_HOME=/app \
    MODELS_DIR=/app/models

WORKDIR /app

# ffmpeg turns reel frames + voiceover into an MP4
RUN apt-get update \
 && apt-get install -y --no-install-recommends fontconfig tzdata ca-certificates ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

# Kokoro TTS model (Apache-2.0), runs offline on the CPU. ~350 MB, downloaded once at build time.
RUN mkdir -p /app/models && python -c "import urllib.request as u; \
b='https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/'; \
[u.urlretrieve(b + f, '/app/models/' + f) for f in ('kokoro-v1.0.onnx', 'voices-v1.0.bin')]"

# Brand fonts (SIL Open Font License, see assets/fonts/OFL-*.txt)
COPY assets ./assets
RUN mkdir -p /usr/share/fonts/truetype/bytebaithak \
 && cp assets/fonts/*.ttf /usr/share/fonts/truetype/bytebaithak/ \
 && fc-cache -f

COPY app ./app
COPY tests ./tests
COPY data/calendar.csv ./defaults/calendar.csv

CMD ["python", "-m", "app.main", "run"]
