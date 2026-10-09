# Image for the inventory API. Build: docker build -t inventory-api .
FROM python:3.12-slim

# No .pyc files in the image; logs appear immediately instead of being buffered.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies BEFORE the code: Docker caches each layer, so editing app code
# only rebuilds the cheap layers below. pip install reruns only when
# requirements.txt changes. Only runtime dependencies: no pytest in production.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

# Never run as root inside the container: if the app were compromised, the
# attacker would not get root privileges in the container.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# Apply migrations, then start the server. "exec" replaces the shell with
# uvicorn, so uvicorn receives Docker's stop signal (SIGTERM) directly and can
# finish in-flight requests before exiting (graceful shutdown).
# Running migrations at startup is fine for one instance; with many replicas
# you'd run them once as a separate deploy step instead.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
