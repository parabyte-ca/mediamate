# ── Stage 1: Build React frontend ─────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /build/frontend

COPY frontend/package*.json ./
RUN npm install

COPY frontend/ ./
# The app reloads itself when the server is newer than the screens it has cached.
ARG APP_VERSION=0.8.0
ENV VITE_APP_VERSION=${APP_VERSION}
RUN npm run build

# ── Stage 2: Python backend + compiled frontend ────────────────────────────────
FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/parabyte-ca/mediamate"
LABEL org.opencontainers.image.description="Self-hosted media pipeline: find, download, encode, split"
LABEL org.opencontainers.image.licenses="MIT"

ARG APP_VERSION=0.8.0
ENV APP_VERSION=${APP_VERSION}

WORKDIR /app

# Install Python deps + curl (needed for HEALTHCHECK) + ffmpeg (media engine)
COPY backend/requirements.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends curl ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/app/ ./app/

# Copy built frontend into /app/static (served by FastAPI)
COPY --from=frontend-builder /build/frontend/dist ./static/

# Data directory (mounted volume in production)
RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
