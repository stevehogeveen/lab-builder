FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        openssh-client \
        sshpass \
        xorriso \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-runtime.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements-runtime.txt

COPY app ./app
COPY static ./static
COPY templates ./templates
COPY VERSION ./VERSION

RUN mkdir -p /app/config/kits /app/artifacts /app/media

VOLUME ["/app/config", "/app/artifacts", "/app/media"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json, urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)); raise SystemExit(0 if data.get('status') == 'ok' else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
