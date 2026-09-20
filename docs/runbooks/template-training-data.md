# Template training data — 2026-09-19

## Scope and current state

Research/download/preparation only. Raw files and prepared JSONL exist locally under
`data/template-catalog/` (git-ignored). No training, deployment, MinIO upload, Postgres
registration, frontend edit, or template API implementation has occurred. Downloaded
data is not yet selectable by a user in the product. Never expose an unowned dataset
as a shortcut around the existing ownership checks.

The frontend's eight template IDs and meanings remain unchanged. Two datasets match
their intended task; four are useful but limited starters; two remain blocked.
User approved **ready-only activation** on 2026-09-19: partial/blocked templates
must display “ยังไม่พร้อม”. Do not activate them or rename their scope implicitly.
Even the two prepared sets are not live until registration and integration succeed.

| ID / template | Train / validation / test | Status and actual coverage |
|---|---:|---|
| tpl-001 Thai Customer Support Classifier | — | Blocked: no verified Thai source covering billing/technical/account/shipping/refund/general with clear reuse permissions |
| tpl-002 Invoice & Receipt QA Extractor | 317 / 44 / 16 | Partial: English synthetic invoices + model-generated silver labels; vendor/date/number/items/tax/totals grounded in OCR text; **no Thai coverage** |
| tpl-003 Function Calling Agent | 162 / 17 / 41 | Partial: English user creation/lookups, product search, two order-tracking training examples; **not full CRUD** |
| tpl-004 Thai Named Entity QA | 4,240 / 500 / 500 | Prepared: Thai PERSON/ORG/LOC/DATE/MONEY extraction as QA with JSON answer and character offsets |
| tpl-005 Product Knowledge Base QA | 329 / 66 / 72 | Domain-limited starter: English IBM technical support with supplied source documents; **not your product knowledge base** |
| tpl-006 Thai Sentiment Analyzer | 6,000 / 600 / 900 | Prepared: positive/neutral/negative, balanced 2,000 training examples/class; question class removed |
| tpl-007 Smart Home Assistant Tool Caller | 1,220 / 131 / 158 | Partial: English lights/locks/temperature with real source tool schemas/device context; **no alarm capability** |
| tpl-008 Medical Symptom Triage | — | Blocked: no verified four-label emergency/urgent/routine/self-care clinical training source; no inferred urgency from disease labels |

Counts are accepted unique examples, not repeated rows to reach a marketing target.
Glaive and Home Assistant are already-synthetic public datasets. We generated no
new synthetic labels and made no paid inference calls. These artifacts establish
format/length compatibility, **not trained-model accuracy or medical safety**.

## Sources and permissions evidence

Exact revisions and SHA-256 are in each prepared directory's `manifest.json`;
source READMEs are retained in `sources/`. Keep `SOURCE_LICENSE.md`, the original
README/notices, and applicable license terms with redistributed artifacts.

| Local directory | Primary source | Declared license |
|---|---|---|
| `sources/wisesight` | [PyThaiNLP Wisesight](https://huggingface.co/datasets/pythainlp/wisesight_sentiment), [original public-domain statement](https://github.com/PyThaiNLP/wisesight-sentiment) | CC0-1.0 |
| `sources/thainer` | [ThaiNER 2.2](https://huggingface.co/datasets/pythainlp/thainer-corpus-v2.2) | CC-BY-3.0; attribute Wannaphong Phatthiyaphaibun, DOI 10.5281/zenodo.10795907; transformation disclosed |
| `sources/glaive` | [Glaive v2](https://huggingface.co/datasets/glaiveai/glaive-function-calling-v2) | Apache-2.0 |
| `sources/home-assistant` | [Home Assistant Requests V2](https://huggingface.co/datasets/acon96/Home-Assistant-Requests-V2) | MIT |
| `sources/techqa` | [NVIDIA TechQA-RAG-Eval](https://huggingface.co/datasets/nvidia/TechQA-RAG-Eval) | Apache-2.0 |
| `sources/invoice` | [clearOCR invoices](https://huggingface.co/datasets/Lukaszl/clearocr-invoice-document-ai), [original Mendeley dataset](https://data.mendeley.com/datasets/tnj49gpmtz/2) | CC-BY-4.0; retain original Kozlowski/Weichbroth and clearOCR attribution |

Licenses above are upstream declarations, not independent legal clearance of every
underlying sentence. ThaiNER's source notes incomplete article-source records.
Public corpora can contain personal names, contact strings or offensive text;
they have **not** undergone a complete PII or safety audit. Before redistributing
or serving trained models, apply the deployment's privacy/content-review policy.

Rejected shortcuts: Thai customer-support translation has inconsistent license
claims versus upstream and no technical class; do not relabel arbitrary categories.
SROIE distribution/rights could not be verified for this workflow. CORD is licensed
but Indonesian receipts with omitted fields, not this template's Thai/English
invoice scope. The acquired clearOCR alternative contains all required fields but
is English only, with model-generated rather than human-gold extraction labels.
Of 423 source invoices, 46 were rejected because at least one answer value could
not be found in the OCR text; substring grounding is not semantic verification.
Three-level medical vignettes cannot be silently mapped to four
clinical urgency labels. These findings are gaps, not assertions that no suitable
dataset could ever exist.

## Reproduce

Run from backend repo root using the existing `.venv`. Preparation needs the backend
schema dependencies, `pyarrow`, `huggingface_hub`, `transformers==4.57.6` and
`jsonschema==4.26.0`. Those last two tools were installed in the local venv;
`jsonschema` is declared in the development extra so converter tests also work in
a clean test environment. No application runtime dependency was added. Transformers
is already a training extra. No Torch/GPU, model weights, HF login or API key is required.

```sh
.venv/bin/python -m scripts.download_template_sources
.venv/bin/python -m scripts.prepare_template_thai
.venv/bin/python -m scripts.prepare_template_tools
.venv/bin/python -m scripts.prepare_template_qa
.venv/bin/python -m scripts.prepare_template_invoice --download
.venv/bin/python -m pytest tests/unit/test_prepare_template_thai.py tests/unit/test_prepare_template_tools.py tests/unit/test_prepare_template_qa.py tests/unit/test_prepare_template_invoice.py -q
```

Downloads are pinned to reviewed immutable revisions, anonymous and restricted to
specific data/card/tokenizer files (approximately 550 MB raw data). No auto-updating
dataset `main` revision, arbitrary remote Python code or paid model invocation.
Invoice acquisition downloads only metadata, OCR, JSON answers and attribution;
omit `--download` on later offline conversions. No invoice images are downloaded.
Preparation recreates only the named prepared artifacts; keep unrelated files out
of these directories. The downloader reuses the Hugging Face local download cache.

Each `prepared/tpl-XXX/` has `train.jsonl`, `validation.jsonl`, `test.jsonl`, aligned
provenance sidecars (never upload sidecars as samples), `manifest.json`, and
`SOURCE_LICENSE.md` or `ATTRIBUTION.md`. Every row validates against `api/schemas/data_formats.py`:

- Classification: `{"text":"...","label":"positive"}`.
- QA / NER: `{"question":"...","answer":"..."}`; NER's answer is a JSON string.
- Tools: `{"question":"...","answer":"{\"name\":\"...\",\"parameters\":{...}}"}`.

Full messages from the existing training formatter are tokenized with
`Qwen/Qwen2.5-1.5B-Instruct@989aa7980e4cf806f80c7fef2b1adb7bc71aa306`, limited to
2,048 tokens including the assistant answer. No text/answer truncation. Source
tool definitions and necessary context remain in each question. Only single-tool
calls validated against original argument schemas are retained; no tool is run.
These token limits are verified for this tokenizer only: rerun length checks if
the chosen model/chat template/system prompt changes.

Original held-out partitions stay held out. Normalized duplicate queries/texts
are excluded across splits. TechQA also isolates connected document filename/content
groups; invoices isolate seller/invoice-number and OCR-content components before
filtering. This is **not** semantic/paraphrase deduplication; Thai sentence corpora do
not supply reliable document grouping. Manifests record exclusions and coverage.

Tool datasets deduplicate **eligible prepared** examples after schema/length
filtering, not every raw source query. In Home Assistant, 87 prepared training
queries and 10 validation queries also occur in rejected raw test records with
different full contexts. There is no overlap with the prepared test inputs or
queries. Use only the supplied prepared test split for evaluation; the untouched
upstream raw test set is **not** approved as an additional clean holdout. Thai and
invoice preparation reserve raw held-out input/document groups before filtering.

## First training and integration

Use the prepared training count as the real upper bound; never fabricate records
to meet an existing UI `dataset_size`. Begin with the Qwen 1.5B 4-bit model supported
by the backend and a short 1–2 epoch trial, compare held-out results, then adjust.
The two larger Thai sets are useful initial SLM corpora; the 162-row tool subset
and 329-row QA subset are smoke-test/starters, not sufficient evidence of broad
capability. More epochs cannot replace missing intents/tools or domain knowledge.

Only `train.jsonl` is a training artifact. The current trainer creates its own
internal validation split; external `validation.jsonl` is a separate development
holdout until a dedicated input is implemented. Reserve `test.jsonl` for final
evaluation; never concatenate it into training or repeatedly tune on it.

Next integration work must enforce the agreed ready-only availability policy and
resolve service/DB target and dataset ownership. Store immutable training files
in MinIO and protected metadata/pointers in Postgres, link template version to its
dataset, and create authorized user-owned access on project creation. Do not train
on the frontend mock counts, and do not mark a template usable before its artifact
is actually registered and accessible. Rating/fork API work remains separate and
has not been implemented in this data-preparation phase.
