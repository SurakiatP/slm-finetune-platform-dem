# 05 — Models Catalog (LLM teachers + supported SLM base models)

> Handoff reference for the frontend team. Two independent groups of models:
> the **cloud LLMs** (accessed via OpenRouter) act as *teachers/orchestrators*
> across the pipeline, and the **SLMs** are the *students* the platform
> fine-tunes and serves via Ollama.
>
> Verified against backend `dev` (`ai_engine/data_gen/models.py`,
> `api/core/config.py`, `api/routers/tasks_meta.py`,
> `api/services/base_model_catalog.py`, `ai_engine/training/unsloth_trainer.py`).

---

## 1. LLM models used per process (all via OpenRouter)

| Process / Stage | Role | Model | Defined at |
|---|---|---|---|
| **Seed upload** | Detect / map schema (canonicalize column names) | `google/gemini-2.5-flash-lite` | `FORMAT_DETECTION` |
| **QA + PDF flow** | Read PDF (multimodal) | `google/gemini-2.5-flash-lite` | `PDF_QA` |
| **SDG – diversity** | Generate diversity rules (1 call / job) | `deepseek/deepseek-v4-flash` | `DIVERSITY_RULES` |
| **SDG – generate** | Generate synthetic data (~100 concurrent) | `deepseek/deepseek-v4-flash` | `GENERATOR` |
| **SDG – filter** | LLM-as-Judge filtering candidates (~500 concurrent) | `deepseek/deepseek-v4-flash` | `JUDGE` |
| **Evaluation** | LLM-judge scoring the fine-tuned model | `qwen/qwen3-235b-a22b-2507` | `config.llm_judge_model` |

The five SDG/upload roles (`FORMAT_DETECTION`, `PDF_QA`, `DIVERSITY_RULES`,
`GENERATOR`, `JUDGE`) are hardcoded in **`ai_engine/data_gen/models.py`** per the
Phase 9 decision (no per-request override). To change one, edit that single
file. The current live values are also exposed read-only at
**`GET /api/v1/sdg-pipeline`** → `{ generator, judge, diversity_rules }`, so the
UI never has to hardcode these strings.

### Two things to watch out for

- **There are two different "judge" models, with different jobs:**
  - **SDG JUDGE** (`deepseek/deepseek-v4-flash`) — filters generated candidates
    *during* synthetic-data generation.
  - **Evaluation judge** (`qwen/qwen3-235b-a22b-2507`) — scores the fine-tuned
    model *during* the evaluation phase (`config.llm_judge_model`).
  The evaluation judge is **deliberately not** switched to deepseek — it is a
  separate concern from SDG generation. It is platform-controlled (not
  user-selectable in the UI).
- **`config.openrouter_teacher_model = anthropic/claude-3.5-sonnet`** is only the
  OpenRouter client's *fallback default* — every real call path overrides it
  with one of the models above, so treat it as a legacy default, not an
  actually-used model.

---

## 2. Supported SLM base models — 10 total

All are **Unsloth 4-bit (bnb-4bit)** variants, **≤ 3.5B params**, sized to fit
QLoRA fine-tuning on the **RTX 3060 12 GB** target (ADR-002). Served to the
frontend at **`GET /api/v1/base-models`** (source: `api/routers/tasks_meta.py`,
list `_RAW_BASE_MODELS` → `SUPPORTED_BASE_MODELS`).

| # | Model | Family | Params | Context | Ollama tag | License |
|---|---|---|---|---|---|---|
| 1 | Llama 3.2 1B Instruct | llama | 1.24B | 131k | `llama3.2:1b` | llama-3.2 |
| 2 | **Llama 3.2 3B Instruct** ⭐ *default* | llama | 3.21B | 131k | `llama3.2:3b` | llama-3.2 |
| 3 | Qwen2.5 0.5B | qwen | 0.49B | 32k | `qwen2.5:0.5b` | apache-2.0 |
| 4 | Qwen2.5 1.5B | qwen | 1.54B | 32k | `qwen2.5:1.5b` | apache-2.0 |
| 5 | Qwen2.5 3B | qwen | 3.09B | 32k | `qwen2.5:3b` | qwen-research |
| 6 | Gemma 2 2B | gemma | 2.61B | 8k | `gemma2:2b` | gemma |
| 7 | Qwen3 0.6B | qwen | 0.75B | 32k | `qwen3:0.6b` | apache-2.0 |
| 8 | Qwen3 1.7B | qwen | 2.03B | 32k | `qwen3:1.7b` | apache-2.0 |
| 9 | SmolLM2 1.7B | smollm | 1.71B | 8k | `smollm2:1.7b` | apache-2.0 |
| 10 | TinyLlama 1.1B Chat | llama | 1.10B | 2k | `tinyllama:1.1b` | apache-2.0 |

**Full HuggingFace / Unsloth id** for each row = `unsloth/<...>-bnb-4bit` — the
`id` field returned by `/api/v1/base-models` (e.g.
`unsloth/Llama-3.2-3B-Instruct-bnb-4bit`). Use that exact `id` as the
`base_model` when launching a training.

### Notes

- **Default base model:** `unsloth/Llama-3.2-3B-Instruct-bnb-4bit`
  (`config.default_base_model`) — used when a training request omits `base_model`.
- **`ollama_tag`** is the Ollama-Hub tag of the *same* instruct weights, used to
  A/B compare a fine-tuned artifact against its base in the playground
  (`base_model_catalog.py`). Numerics differ slightly from the training
  quantization, so it's for demo comparison, not scientific benchmarking. Value
  is `null` if no Ollama-Hub equivalent is mapped.
- **Chat templates** are resolved for three families in
  `ai_engine/training/unsloth_trainer.py`: `llama-3.2`, `qwen2.5` (chatml),
  `gemma-2`. Qwen3 / SmolLM2 / TinyLlama map onto the nearest matching template.
- **Task types** the platform supports are fixed at 3: `classification`,
  `tool_calling`, `qa` (ADR-005) — independent of base-model choice.

### Frontend integration hints

- Build the model picker from `GET /api/v1/base-models` — **don't hardcode** this
  list; new models get added backend-side and will appear automatically.
- `BaseModelInfo` fields available per row: `id`, `display_name`, `family`,
  `params_billions`, `context_length`, `recommended_max_seq_length`,
  `quantization` (`"bnb-4bit"`), `license`, `notes`, `ollama_tag`.
- Show `notes` as helper text and `params_billions` / `context_length` as badges
  to help users pick.

---

## Source-of-truth files

| Concern | File |
|---|---|
| SDG/upload LLM roles (5) | `ai_engine/data_gen/models.py` |
| Live SDG models endpoint | `GET /api/v1/sdg-pipeline` (`api/routers/tasks_meta.py`) |
| Evaluation judge + defaults | `api/core/config.py` (`llm_judge_model`, `default_base_model`, `openrouter_teacher_model`) |
| Supported SLM base models | `api/routers/tasks_meta.py` (`_RAW_BASE_MODELS`), served at `GET /api/v1/base-models` |
| Base → Ollama tag mapping | `api/services/base_model_catalog.py` |
| Chat templates / EOS per family | `ai_engine/training/unsloth_trainer.py` |
