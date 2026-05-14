# India Research Monitor — single image, multi-command.
# Build:  docker build -f research_system/deploy/Dockerfile -t research-monitor .
# Run individually:
#   docker run --rm --env-file .env -v $(pwd)/data:/app/research_system/data \
#       research-monitor scheduler
#   docker run --rm --env-file .env ... research-monitor telegram
#   docker run --rm -p 8501:8501 --env-file .env ... research-monitor dashboard

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Kolkata

# Build deps for lxml/yfinance; tzdata for zoneinfo on slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libxml2-dev libxslt1-dev tzdata curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching
COPY research_system/requirements.txt /app/research_system/requirements.txt
RUN pip install -r /app/research_system/requirements.txt

# Copy source
COPY research_system /app/research_system

# Initialise DB at build time so first run starts clean
RUN python -m research_system.db

EXPOSE 8501
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8501/ >/dev/null 2>&1 || \
      python -c "import sqlite3,os; sqlite3.connect('/app/research_system/data/research.db').execute('SELECT 1').fetchone()" || exit 1

# Entry script lets one image do all three roles
COPY research_system/deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["scheduler"]
