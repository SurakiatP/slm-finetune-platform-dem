"""LLM model identifiers used by the SDG pipeline.

Hardcoded per Phase 9 decision Q6.1: no per-request override. To change a
model, edit this file (and write/update an ADR if the change is non-trivial).
"""

from __future__ import annotations

# Schema-mapping assistant — runs once per seed upload to canonicalise key names.
# Cheap multimodal model; sub-second latency is expected.
FORMAT_DETECTION = "google/gemini-2.5-flash-lite"

# Multimodal model used for the first iteration of QA + PDF flow.
# Same SKU as FORMAT_DETECTION because it accepts PDF inputs natively via
# OpenRouter's OpenAI-compatible multimodal API.
PDF_QA = "google/gemini-2.5-flash-lite"

# All three SDG roles run the same SKU, pinned to the dated `-0731` snapshot
# rather than the rolling `deepseek/deepseek-v4-flash` alias: SDG output feeds
# straight into a fine-tune, so a silent upstream model swap would change what
# the trained model learns with nothing in the run's metadata to explain it.

# Diversity-rule generator (meta-prompting). One call per SDG job.
DIVERSITY_RULES = "deepseek/deepseek-v4-flash-0731"

# Synthetic data Generator. Up to ~100 concurrent calls per loop iteration.
GENERATOR = "deepseek/deepseek-v4-flash-0731"

# LLM-as-Judge. Up to ~500 concurrent calls per loop iteration
# (5 candidates per generator call × 100 calls).
JUDGE = "deepseek/deepseek-v4-flash-0731"


__all__ = [
    "FORMAT_DETECTION",
    "PDF_QA",
    "DIVERSITY_RULES",
    "GENERATOR",
    "JUDGE",
]
