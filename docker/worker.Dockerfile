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
        cmake \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pre-build llama.cpp so Unsloth.save_pretrained_gguf() finds llama-quantize at
# /app/llama.cpp/llama-quantize. Without this, Unsloth falls through to its
# interactive install_llama_cpp() which calls input() for a sudo password and
# crashes Celery workers (no stdin) with EOFError. CPU-only build is enough —
# quantization is CPU-bound; GGML_CUDA=ON would also fail because the runtime
# base image has no nvcc.
RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp.git /app/llama.cpp \
    && cd /app/llama.cpp \
    && cmake -B build -DGGML_CUDA=OFF -DLLAMA_CURL=OFF -DBUILD_SHARED_LIBS=OFF \
    # `-j` with no number means `make -j` = UNLIMITED parallel jobs. llama.cpp's
    # big translation units (llama.cpp, ggml) cost ~2-3 GB each to compile, so
    # unbounded parallelism fork-bombs the compiler and exhausts RAM — it OOM's
    # both the 15 GB pasaflow VM (the original "VM reboots on build") and the
    # 16 GB CI runner ("runner lost communication / starved for memory"). Cap at
    # 2: bounded peak memory, negligible time cost for this small target.
    && cmake --build build --config Release -j 2 --target llama-quantize \
    && cp build/bin/llama-quantize /app/llama.cpp/llama-quantize \
    && (ldd /app/llama.cpp/llama-quantize 2>&1 | grep -q "not found" \
        && (echo "ERROR: llama-quantize has unresolved shared libs after static build:" \
            && ldd /app/llama.cpp/llama-quantize \
            && exit 1) \
        || true) \
    && rm -rf build

# Install base + training extras (unsloth, transformers, peft, trl, bitsandbytes, ...).
# `gguf` is needed by /app/llama.cpp/convert_hf_to_gguf.py (the Python conversion
# script Unsloth invokes before quantizing). Install it explicitly rather than via
# llama.cpp/requirements.txt to avoid downgrading torch to that file's old pin.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install gguf && pip install -e ".[training,eval]"

COPY api ./api
COPY ai_engine ./ai_engine
COPY workers ./workers

CMD ["celery", "-A", "workers.celery_app", "worker", "--loglevel=INFO", "--concurrency=1"]
