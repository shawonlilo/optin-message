# Opt-In Message — the Python web app (engine + dashboard) in a container.
#
# The app is standard-library only, so the image stays small: no pip
# installs, no build tools. It runs as a non-root user, keeps its SQLite
# database on a volume at /data, and starts in DRY_RUN mode — a fresh
# container never sends a real message. See BUILDING.md for the full
# Docker walkthrough.
FROM python:3.12-slim

# HOST must be 0.0.0.0 inside a container or the published port cannot
# reach the server (local runs keep app.py's 127.0.0.1 default).
ENV HOST=0.0.0.0 \
    PORT=8080 \
    DATABASE_PATH=/data/optin.db \
    DRY_RUN=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Only what the web app needs at runtime: the server, the AI module and
# the pages it serves.
COPY app.py ai.py ./
COPY gui/ ./gui/

# Non-root user, and it owns the data directory the volume mounts on.
RUN useradd --create-home appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data
USER appuser

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

CMD ["python3", "app.py"]
