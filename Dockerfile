FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npx vite build

FROM python:3.13-slim
LABEL org.opencontainers.image.title="AudioBook Wizarrd" org.opencontainers.image.version="0.7.1"
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt backend/requirements-whisper.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Optional Spracherkennung (gross): compose ABW_WHISPER=1
ARG WHISPER=0
RUN if [ "$WHISPER" = "1" ]; then pip install --no-cache-dir -r requirements-whisper.txt; fi
# Zusätzliche Pakete für Erweiterungen (compose: ABW_EXTRA_PIP)
ARG EXTRA_PIP=""
RUN if [ -n "$EXTRA_PIP" ]; then pip install --no-cache-dir $EXTRA_PIP; fi
COPY backend/abw ./abw
COPY --from=ui /ui/dist ./static
ENV ABW_STATIC_DIR=/app/static ABW_DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8790
CMD ["uvicorn", "abw.api:app", "--host", "0.0.0.0", "--port", "8790"]
