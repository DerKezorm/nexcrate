# nexcrate as one container.
#
# Two stages: the interface is built with Node first, then only the finished files go into
# the image. Node and node_modules stay out; FastAPI serves the built files.

# --- Stage 1: build the interface --------------------------------------------------
#
# --platform=$BUILDPLATFORM: the image is built for amd64 and arm64. Without it this stage
# would run emulated as well, and npm ci under emulated ARM is very slow. Only /build/dist
# moves on: HTML, CSS and JavaScript, without an architecture.
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend

WORKDIR /build

# Dependencies first: as long as they stay the same, Docker reuses the layer and skips npm ci.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# --- Stage 2: runtime ----------------------------------------------------------------
# The same Python version as the development environment the tests run on.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXCRATE_DATA_DIR=/data \
    NEXCRATE_FRONTEND_DIR=/app/static

WORKDIR /app

# curl for the healthcheck, gosu to drop root rights at start.
# unrar-free (the tool rarfile uses as "unrar") and 7zip for unpacking downloads: free packages of Debian main only.
# mediainfo (MediaInfoLib 25.04, BSD-2) reads media data of files in a child process; not pymediainfo, which would load
# the library into nexcrate's own process (docs/plan-library-from-disk.md, decision 21).
# ⚠️ Never bsdtar or libarchive for RAR: bsdtar exited 0 on multi-volume RAR5 with a missing or wrong file.
# libchromaprint-tools brings fpcalc for fingerprinting music files nexcrate cannot map otherwise (docs/plan-music-m4.md,
# M4.9), in a child process like mediainfo. It pulls FFmpeg's libraries: measured 146 MB.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gosu unrar-free 7zip mediainfo libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /build/dist ./static

# The user nexcrate runs as. The container starts as root and drops the rights in the
# entrypoint, see there.
RUN useradd --system --create-home --uid 1000 nexcrate \
    && mkdir -p /data \
    && chown -R nexcrate:nexcrate /data /app

COPY docker/entrypoint.sh /entrypoint.sh
# Strips Windows line endings: checked out on Windows, the script would not start otherwise.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

VOLUME ["/data"]
# Informational only. The real port comes from NEXCRATE_PORT.
EXPOSE 8390

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${NEXCRATE_PORT:-8390}/api/health" || exit 1

# ⚠️ One worker process: SQLite, the in-memory login brake and the background jobs must exist once.
# No access log: nexcrate writes its own request lines, with masked query strings.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--workers", "1", "--no-access-log"]
