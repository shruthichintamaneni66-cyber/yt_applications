FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOWNLOAD_DIR=/downloads \
    REQUIRE_AUTH=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app /downloads \
    && chown -R app:app /app /downloads

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app trail_3_web.py ./

USER app
EXPOSE 8000
CMD ["gunicorn", "--workers=1", "--threads=8", "--timeout=60", "--bind=0.0.0.0:8000", "trail_3_web:app"]
