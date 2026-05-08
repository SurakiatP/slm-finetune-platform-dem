# API container — FastAPI + uvicorn. CPU-only.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer — uses pyproject.toml. Reinstall only when it changes.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install -e .

# Source is bind-mounted via docker-compose for `--reload`. The COPY
# below makes the image self-contained for non-compose runs.
COPY api ./api
COPY ai_engine ./ai_engine
COPY workers ./workers
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
