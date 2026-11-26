FROM python:3.11-slim

# Install ffmpeg (with libass), nodejs (JS runtime for yt-dlp EJS), and libsndfile1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY cli.py ./cli.py

ENV CLIPFORGE_DATA_DIR=/app/data
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
