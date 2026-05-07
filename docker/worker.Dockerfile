# Celery worker container — needs CUDA + torch for Unsloth/QLoRA fine-tuning.
# Base image already provides Python 3.11 + torch 2.5.1 + CUDA 12.1 runtime.
FROM pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HF_HOME=/root/.cache/huggingface

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install base + training extras (unsloth, transformers, peft, trl, bitsandbytes, ...).
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install -e ".[training,eval]"

COPY api ./api
COPY ai_engine ./ai_engine
COPY workers ./workers

CMD ["celery", "-A", "workers.celery_app", "worker", "--loglevel=INFO", "--concurrency=1"]
