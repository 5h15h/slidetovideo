# Renderer for SlideToVideo. Needs LibreOffice, poppler and ffmpeg, which is
# why this half cannot run on a serverless platform.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    PPT2VIDEO_JOBS=/data/jobs

RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-impress \
      poppler-utils \
      ffmpeg \
      fonts-dejavu-core \
      fonts-liberation2 \
      fonts-crosextra-carlito \
      fonts-crosextra-caladea \
      fonts-noto-core \
      fonts-inter \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pipeline/ ./pipeline/
COPY webapp/ ./webapp/
RUN mkdir -p /data/jobs

EXPOSE 8808
# ONE worker on purpose: job state lives in memory in this process, so a
# second worker would answer polls for jobs it has never heard of. Threads
# give concurrency; renders run in background threads of this same worker.
CMD exec gunicorn --chdir /app webapp.server:app \
      --bind "0.0.0.0:${PORT:-8808}" \
      --workers 1 --threads 16 \
      --timeout 120 --graceful-timeout 30 \
      --access-logfile - --error-logfile -
