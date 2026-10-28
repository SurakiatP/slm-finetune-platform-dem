# GPU worker for ARM64 + Blackwell hosts (NVIDIA GB10 / DGX Spark, e.g. gx10).
# docker/worker.Dockerfile stays the x86 build; this one differs only in how
# torch and the training stack get installed.
#
# Why NGC instead of pytorch/pytorch: that image is amd64-only, and pip's
# aarch64 torch wheel is CPU-only. NGC ships a CUDA 13 torch built for sm_121.
# Install order follows NVIDIA's DGX Spark Unsloth playbook: the HF stack
# first (normal deps), then unsloth/unsloth_zoo/bitsandbytes with --no-deps so
# their x86-only pins can't replace NGC's torch.
FROM nvcr.io/nvidia/pytorch:25.11-py3

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HF_HOME=/root/.cache/huggingface \
    HF_HUB_DISABLE_XET=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Same llama-quantize prebuild as docker/worker.Dockerfile (see the comments
# there): Unsloth's GGUF export otherwise tries an interactive install.
RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp.git /app/llama.cpp \
    && cd /app/llama.cpp \
    && cmake -B build -DGGML_CUDA=OFF -DLLAMA_CURL=OFF -DBUILD_SHARED_LIBS=OFF \
    && cmake --build build --config Release -j 4 --target llama-quantize \
    && cp build/bin/llama-quantize /app/llama.cpp/llama-quantize \
    && rm -rf build

# Pinned as a set: unpinned latest (transformers 5.x / huggingface_hub 1.x /
# unsloth 2026.10) fails at import ("cannot import name ResolvedRevision").
# This combo was verified on gx10 (GB10 capability 12.1) on 2026-10-28.
# Appended to NGC's own PIP_CONSTRAINT file so the later `.[eval]` install
# can't upgrade them either.
RUN printf '%s\n' "transformers==4.57.1" "huggingface_hub<1.0" "peft==0.18.0" \
        "trl==0.24.0" "datasets==4.3.0" >> "$PIP_CONSTRAINT" \
    && pip install --upgrade pip \
    && pip install gguf accelerate hf_transfer sentencepiece msgspec \
        transformers huggingface_hub peft trl datasets \
    && pip install --no-deps "unsloth==2025.11.1" "unsloth_zoo==2025.11.1" \
        bitsandbytes cut_cross_entropy

# App deps without the `training` extra: torch>=2.5.1 there would let pip
# swap NGC's CUDA torch for the CPU wheel. NGC's PIP_CONSTRAINT pins torch too.
COPY pyproject.toml README.md ./
RUN pip install -e ".[eval]"

COPY api ./api
COPY ai_engine ./ai_engine
COPY workers ./workers

CMD ["celery", "-A", "workers.celery_app", "worker", "--loglevel=INFO", "--concurrency=1"]
