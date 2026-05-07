FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Versions pinned to ADR-001 / docs/architecture/TECH_STACK.md
RUN pip install \
        "mlflow==2.19.0" \
        "psycopg2-binary==2.9.10" \
        "boto3>=1.35.0"

EXPOSE 5000
