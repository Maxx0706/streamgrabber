FROM python:3.12-slim

# ffmpeg is needed for HD video and MP3
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# always grab the newest yt-dlp, sites change constantly
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -U yt-dlp

COPY app.py index.html ./

ENV PORT=8080
EXPOSE 8080

# one worker (downloads are tracked in memory), many threads for many users
CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 16 --timeout 600
