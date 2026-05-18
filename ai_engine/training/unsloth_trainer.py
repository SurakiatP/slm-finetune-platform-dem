"""Unsloth + QLoRA fine-tuning wrapper.

Pure domain code; the Celery task in `workers/tasks/training.py` is the only
caller. All heavy deps (`unsloth`, `torch`, `transformers`, `trl`, `datasets`,
`peft`) are imported lazily inside `UnslothTrainer.train()` so this module
stays importable on hosts without the `[training]` extras (the API process,
unit tests, etc.).

Per ADR-002 every load passes `load_in_4bit=True`. GPU cleanup is the caller's
responsibility (the Celery task wraps `train()` in a `finally` block that
runs `torch.cuda.empty_cache()` + `gc.collect()`).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from api.schemas.data_formats import ToolDefinition
from api.schemas.enums import TaskType
from api.schemas.training import LoRAConfig, ManualTrainingConfig

from .data_formatters import get_formatter

if TYPE_CHECKING:  # pragma: no cover — type-only imports
    from transformers import TrainerCallback

log = logging.getLogger(__name__)


# ---- Chat-template mapping --------------------------------------------------

# Unsloth's `get_chat_template()` accepts a chat-template *name* (not the
# tokenizer's raw Jinja). The mapping below resolves the supported base
# models (see `api/routers/tasks_meta.py`) to the matching template name
# from `unsloth.chat_templates.CHAT_TEMPLATES`.
_CHAT_TEMPLATE_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("unsloth/llama-3.2", "llama-3.2"),
    ("unsloth/qwen2.5", "chatml"),
    ("unsloth/gemma-2", "gemma-2"),
)


def _chat_template_for(base_model: str) -> str:
    """Return the Unsloth chat-template name for a given base model id.

    Falls back to `chatml` for unknown ids — every Unsloth-shipped 4-bit
    instruct model has a chat template under that name as a safe default.
    """
    key = base_model.lower()
    for prefix, template in _CHAT_TEMPLATE_BY_PREFIX:
        if key.startswith(prefix):
            return template
    return "chatml"


# Real EOS strings per family — used as the last-resort fallback when the
# tokenizer ships the literal `'<EOS_TOKEN>'` placeholder Unsloth uses as a
# chat-template substitution sentinel. (TRL >=0.20's vocab validator on
# SFTConfig.eos_token rejects the placeholder.)
_EOS_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("unsloth/llama-3.2", "<|eot_id|>"),
    ("unsloth/qwen2.5", "<|im_end|>"),
    ("unsloth/gemma-2", "<end_of_turn>"),
)


def _resolve_eos_token(tokenizer: Any, base_model: str) -> str:
    """Return a real EOS string that exists in the tokenizer vocab.

    Order of preference:
      1. `tokenizer.eos_token` if it's not the `<EOS_TOKEN>` placeholder and
         actually decodes to a vocab entry.
      2. The string for `tokenizer.eos_token_id` via `convert_ids_to_tokens`.
      3. Hard-coded per-family fallback (`<|eot_id|>` / `<|im_end|>` /
         `<end_of_turn>`).
    """
    eos = getattr(tokenizer, "eos_token", None)
    vocab = tokenizer.get_vocab() if hasattr(tokenizer, "get_vocab") else {}
    if eos and eos != "<EOS_TOKEN>" and eos in vocab:
        return eos
    eos_id = getattr(tokenizer, "eos_token_id", None)
    if eos_id is not None:
        try:
            token = tokenizer.convert_ids_to_tokens(int(eos_id))
        except Exception:  # noqa: BLE001 — tolerate odd tokenizer impls
            token = None
        if token and token in vocab:
            return token
    key = base_model.lower()
    for prefix, fallback in _EOS_BY_PREFIX:
        if key.startswith(prefix):
            return fallback
    raise RuntimeError(
        f"Cannot resolve a real EOS token for base_model={base_model!r}; "
        f"tokenizer.eos_token={eos!r}, eos_token_id={eos_id!r}"
    )


# ---- Result type -----------------------------------------------------------


@dataclass
class TrainingResult:
    """Outcome of a single training run.

    `adapter_dir` is a local filesystem path; the worker uploads its contents
    to MinIO and stores the resulting `s3://...` URI on `ModelArtifact`.
    """

    adapter_dir: str
    final_train_loss: float | None
    final_eval_loss: float | None
    train_runtime_seconds: float | None
    train_samples_per_second: float | None
    steps_completed: int
    metrics: dict[str, float] = field(default_factory=dict)


# ---- Trainer ---------------------------------------------------------------


class UnslothTrainer:
    """Wraps Unsloth's `FastLanguageModel` + TRL's `SFTTrainer`.

    The class is intentionally thin — it holds no global state and is
    constructed fresh per Celery task. All knobs come from `ManualTrainingConfig`.
    """

    def __init__(
        self,
        *,
        base_model: str,
        config: ManualTrainingConfig,
        task_type: TaskType,
        tool_definitions: list[ToolDefinition] | None = None,
        output_dir: str,
    ) -> None:
        self.base_model = base_model
        self.config = config
        self.task_type = task_type
        self.tool_definitions = tool_definitions
        self.output_dir = output_dir

    # -- Public ----------------------------------------------------------------

    def train(
        self,
        rows: list[dict[str, Any]],
        *,
        callbacks: list["TrainerCallback"] | None = None,
        eval_split: float = 0.1,
    ) -> TrainingResult:
        """Run one fine-tune.

        Args:
            rows: training examples (already validated; one dict per sample).
            callbacks: HuggingFace `TrainerCallback`s — typically a progress
                callback built by `ai_engine.training.callbacks.make_progress_callback`.
            eval_split: fraction (0–0.5) reserved for eval. Set to 0 to skip eval.
        """
        if not rows:
            raise ValueError("UnslothTrainer.train: rows is empty")
        if not 0.0 <= eval_split < 0.5:
            raise ValueError("eval_split must be in [0.0, 0.5)")

        # Deferred imports — only available in the worker container.
        # CRITICAL: `import unsloth` MUST run before `from trl import ...` so
        # Unsloth's monkey-patches replace `trl.SFTConfig` / `trl.SFTTrainer`
        # before we bind them to local names. Reverse the order and the
        # locals point to TRL stock classes that don't know about Unsloth's
        # `<EOS_TOKEN>` chat-template sentinel handling.
        from unsloth import FastLanguageModel
        from unsloth.chat_templates import get_chat_template
        from datasets import Dataset
        from trl import SFTConfig, SFTTrainer

        os.makedirs(self.output_dir, exist_ok=True)

        # ---- 2. Load 4-bit base model + tokenizer (ADR-002) -------------------
        log.info(
            "loading base model: %s (4-bit, max_seq=%d)",
            self.base_model,
            self.config.max_seq_length,
        )
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=self.base_model,
            max_seq_length=self.config.max_seq_length,
            dtype=None,  # auto: bf16 on Ampere+, fp16 otherwise
            load_in_4bit=True,
        )

        # Apply the matching chat template so `apply_chat_template()` renders
        # rows with the right special tokens at SFTTrainer time.
        chat_template = _chat_template_for(self.base_model)
        log.info("applying chat template: %s", chat_template)
        tokenizer = get_chat_template(tokenizer, chat_template=chat_template)

        # Unsloth's 4-bit ports ship `tokenizer.eos_token = '<EOS_TOKEN>'` as
        # a chat-template substitution sentinel. `get_chat_template()` does
        # not always replace it on the tokenizer object itself, and TRL's
        # SFTTrainer copies that placeholder onto SFTConfig.eos_token, where
        # the vocab validator rejects it. Resolve a real EOS string here and
        # pass it to SFTConfig explicitly below.
        eos_token = _resolve_eos_token(tokenizer, self.base_model)
        log.info("resolved eos_token: %s", eos_token)

        # ---- 1b. Render messages → plain text via the chat template -----------
        # Pre-rendering bypasses Unsloth's `formatting_func` requirement on
        # `messages` datasets and lets us feed a plain `text` field that all
        # SFTConfig versions (stock TRL + Unsloth-patched) handle uniformly.
        formatter = get_formatter(
            self.task_type, tool_definitions=self.tool_definitions
        )
        texts: list[dict[str, str]] = [
            {
                "text": tokenizer.apply_chat_template(
                    formatter(row), tokenize=False, add_generation_prompt=False
                )
            }
            for row in rows
        ]

        ds = Dataset.from_list(texts)
        if eval_split > 0.0 and len(ds) >= 4:
            split = ds.train_test_split(
                test_size=eval_split, seed=self.config.seed, shuffle=True
            )
            train_ds, eval_ds = split["train"], split["test"]
        else:
            train_ds, eval_ds = ds, None

        # ---- 3. Attach LoRA adapters ------------------------------------------
        lora: LoRAConfig = self.config.lora
        model = FastLanguageModel.get_peft_model(
            model,
            r=lora.r,
            lora_alpha=lora.alpha,
            lora_dropout=lora.dropout,
            target_modules=list(lora.target_modules),
            bias="none",
            use_gradient_checkpointing="unsloth",
            random_state=self.config.seed,
            use_rslora=False,
            loftq_config=None,
        )

        # ---- 4. SFTConfig ------------------------------------------------------
        # SFTConfig (TRL >=0.13) extends TrainingArguments and absorbs the
        # SFT-specific knobs (`max_length`, `packing`, `dataset_text_field`).
        sft_kwargs: dict[str, Any] = dict(
            output_dir=self.output_dir,
            per_device_train_batch_size=self.config.per_device_train_batch_size,
            gradient_accumulation_steps=self.config.gradient_accumulation_steps,
            num_train_epochs=self.config.num_train_epochs,
            learning_rate=self.config.learning_rate,
            warmup_ratio=self.config.warmup_ratio,
            weight_decay=self.config.weight_decay,
            lr_scheduler_type=self.config.lr_scheduler_type,
            seed=self.config.seed,
            logging_steps=1,
            save_strategy="no",                  # Worker handles persistence to MinIO.
            eval_strategy="epoch" if eval_ds is not None else "no",
            optim=self.config.optim,
            bf16=_supports_bf16(),
            fp16=not _supports_bf16(),
            report_to=[],                        # MLflow is wired via callback, not HF integration.
            disable_tqdm=True,                   # Progress streams via callback.
            dataset_text_field="text",
            max_length=self.config.max_seq_length,
            packing=self.config.packing,
            eos_token=eos_token,
        )
        # NEFTune is opt-in — passing 0/None to SFTConfig still enables the
        # wrapper, so only set the kwarg when the user actually wants noise.
        if self.config.neftune_noise_alpha is not None:
            sft_kwargs["neftune_noise_alpha"] = self.config.neftune_noise_alpha
        sft_config = SFTConfig(**sft_kwargs)

        # ---- 5. SFT trainer ----------------------------------------------------
        # TRL >=0.12 renamed `tokenizer=` to `processing_class=` (hard-removed
        # in 0.16). All SFT-specific kwargs now live on `sft_config` above.
        trainer = SFTTrainer(
            model=model,
            processing_class=tokenizer,
            args=sft_config,
            train_dataset=train_ds,
            eval_dataset=eval_ds,
            callbacks=list(callbacks or []),
        )

        # ---- 6. Train + save adapter ------------------------------------------
        log.info(
            "starting fine-tune: rows=%d epochs=%d batch=%d ga=%d",
            len(rows),
            self.config.num_train_epochs,
            self.config.per_device_train_batch_size,
            self.config.gradient_accumulation_steps,
        )
        train_output = trainer.train()
        eval_metrics: dict[str, float] = {}
        if eval_ds is not None:
            eval_metrics = {
                k: float(v)
                for k, v in trainer.evaluate().items()
                if isinstance(v, (int, float))
            }

        adapter_dir = os.path.join(self.output_dir, "adapter")
        trainer.save_model(adapter_dir)  # writes adapter_config.json + adapter_model.safetensors
        tokenizer.save_pretrained(adapter_dir)

        # ---- 7. Build result ---------------------------------------------------
        train_metrics: dict[str, float] = {
            k: float(v)
            for k, v in (train_output.metrics or {}).items()
            if isinstance(v, (int, float))
        }
        merged: dict[str, float] = {**train_metrics, **eval_metrics}

        return TrainingResult(
            adapter_dir=adapter_dir,
            final_train_loss=merged.get("train_loss"),
            final_eval_loss=merged.get("eval_loss"),
            train_runtime_seconds=merged.get("train_runtime"),
            train_samples_per_second=merged.get("train_samples_per_second"),
            steps_completed=int(train_output.global_step or 0),
            metrics=merged,
        )


# ---- helpers ---------------------------------------------------------------


def _supports_bf16() -> bool:
    """True if the current GPU has bf16 (Ampere or newer)."""
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        major, _minor = torch.cuda.get_device_capability()
        return major >= 8
    except Exception:  # noqa: BLE001 — best-effort dtype selection
        return False


# ---- public API ------------------------------------------------------------

ProgressCallbackFactory = Callable[..., "TrainerCallback"]

__all__ = [
    "TrainingResult",
    "UnslothTrainer",
]
