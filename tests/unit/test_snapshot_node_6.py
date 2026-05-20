"""Tier 1 characterization snapshots for Node 6 (Training).

Wraps the pure helpers from `ai_engine/training/unsloth_trainer.py` and
`workers/tasks/training.py` BEFORE refactoring those files. Snapshot diff = 0
post-refactor ⇒ pure-helper behaviour preserved.

Per `docs/runbooks/snapshot_harness.md` §6 ("GPU/Unsloth ไม่ mock") we
deliberately stay in Tier 1 here — the actual `UnslothTrainer.train()` body
is gated behind Tier 3 vast.ai verification, not unit snapshots. The Tier 1
surface we cover:

  * `_resolve_eos_token(tokenizer, base_model)` — fallback chain across
    (a) tokenizer.eos_token in vocab, (b) eos_token_id → token lookup,
    (c) hard-coded per-family fallback, (d) RuntimeError when all fail.
  * `_chat_template_for(base_model)` — prefix lookup with chatml fallback.
  * `_CHAT_TEMPLATE_BY_PREFIX` and `_EOS_BY_PREFIX` table inspection — the
    tuples are intentionally `("unsloth/...", "<template>")` form per the
    module docstring; snapshot freezes the supported base-model coverage.

Note on `workers/tasks/training.py:_extract_tool_definitions` — its sibling
in `workers/tasks/evaluation.py` is already snapshotted by
`test_snapshot_node_9.py` and the two implementations are intentionally
identical. We don't import the worker module here because its top-level
`mlflow` import isn't available in every dev environment; importing only
`ai_engine.training.unsloth_trainer` keeps Tier 1 snapshots runnable on
any host with the base extras.
"""

from __future__ import annotations

import pytest

from ai_engine.training.unsloth_trainer import (
    _CHAT_TEMPLATE_BY_PREFIX,
    _EOS_BY_PREFIX,
    _chat_template_for,
    _resolve_eos_token,
)


# ---- Fake tokenizer --------------------------------------------------------


class _FakeTokenizer:
    """Minimal stand-in for an HF tokenizer — covers only the attrs
    `_resolve_eos_token` reads."""

    def __init__(
        self,
        *,
        eos_token: str | None,
        eos_token_id: int | None,
        vocab: dict[str, int],
        id_to_token: dict[int, str] | None = None,
    ) -> None:
        self.eos_token = eos_token
        self.eos_token_id = eos_token_id
        self._vocab = vocab
        self._id_to_token = id_to_token or {}

    def get_vocab(self) -> dict[str, int]:
        return self._vocab

    def convert_ids_to_tokens(self, idx: int) -> str | None:
        return self._id_to_token.get(idx)


# ---- Tier 1: _chat_template_for --------------------------------------------


@pytest.mark.parametrize(
    "base_model",
    [
        "unsloth/Llama-3.2-1B-Instruct-bnb-4bit",
        "unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
        "unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit",
        "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit",
        "unsloth/Qwen2.5-3B-Instruct-bnb-4bit",
        "unsloth/gemma-2-2b-it-bnb-4bit",
        "unsloth/SmolLM2-1.7B-Instruct-bnb-4bit",     # unknown → chatml fallback
        "meta-llama/Llama-3.2-1B-Instruct",            # non-unsloth prefix → chatml
        "UNSLOTH/LLAMA-3.2-1B-INSTRUCT-BNB-4BIT",      # case-insensitive lookup
        "",                                             # degenerate → chatml
    ],
)
def test_chat_template_for_all_supported(snapshot, base_model: str):
    """One snapshot per supported base — guards against silent template drift."""
    assert _chat_template_for(base_model) == snapshot


def test_chat_template_by_prefix_table(snapshot):
    """Freeze the entire (prefix, template) coverage table."""
    # Snapshot as sorted list so ordering changes are flagged.
    assert sorted(_CHAT_TEMPLATE_BY_PREFIX) == snapshot


def test_eos_by_prefix_table(snapshot):
    """Freeze the per-family EOS fallback table — these strings MUST stay in
    the corresponding tokenizer vocab or TRL >=0.20 SFTConfig rejects them."""
    assert sorted(_EOS_BY_PREFIX) == snapshot


# ---- Tier 1: _resolve_eos_token --------------------------------------------


def test_resolve_eos_token_uses_real_eos_when_in_vocab(snapshot):
    """Happy path: tokenizer.eos_token is a real vocab entry."""
    tok = _FakeTokenizer(
        eos_token="<|eot_id|>",
        eos_token_id=128009,
        vocab={"<|eot_id|>": 128009, "<|begin_of_text|>": 128000},
    )
    assert _resolve_eos_token(tok, "unsloth/Llama-3.2-1B-Instruct-bnb-4bit") == snapshot


def test_resolve_eos_token_skips_placeholder_uses_id_lookup(snapshot):
    """If tokenizer.eos_token is the `<EOS_TOKEN>` Unsloth placeholder,
    fall through to convert_ids_to_tokens lookup."""
    tok = _FakeTokenizer(
        eos_token="<EOS_TOKEN>",
        eos_token_id=151645,
        vocab={"<|im_end|>": 151645, "<|im_start|>": 151644},
        id_to_token={151645: "<|im_end|>"},
    )
    assert _resolve_eos_token(tok, "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit") == snapshot


def test_resolve_eos_token_falls_back_to_per_family_table(snapshot):
    """When both the eos_token and the id-lookup miss the vocab, use the
    hard-coded per-family fallback string."""
    samples: dict[str, str] = {}
    cases = [
        ("unsloth/Llama-3.2-1B-Instruct-bnb-4bit", {"x": 1}),
        ("unsloth/Qwen2.5-3B-Instruct-bnb-4bit", {"x": 1}),
        ("unsloth/gemma-2-2b-it-bnb-4bit", {"x": 1}),
    ]
    for base, vocab in cases:
        tok = _FakeTokenizer(
            eos_token="<EOS_TOKEN>", eos_token_id=None, vocab=vocab
        )
        samples[base] = _resolve_eos_token(tok, base)
    assert samples == snapshot


def test_resolve_eos_token_raises_when_no_fallback_matches():
    """Unknown base + missing tokenizer info → loud RuntimeError, not silent
    bad EOS that TRL's vocab validator will reject downstream."""
    tok = _FakeTokenizer(
        eos_token="<EOS_TOKEN>", eos_token_id=None, vocab={"x": 1}
    )
    with pytest.raises(RuntimeError, match="Cannot resolve a real EOS token"):
        _resolve_eos_token(tok, "huggingface/some-unknown-model")


def test_resolve_eos_token_tolerates_id_conversion_exception(snapshot):
    """`convert_ids_to_tokens` is wrapped in `except Exception` — odd
    tokenizer impls (sentencepiece variants) shouldn't blow up the
    resolver before it can reach the per-family fallback."""

    class _ExplodingTokenizer(_FakeTokenizer):
        def convert_ids_to_tokens(self, idx: int) -> str | None:  # noqa: ARG002
            raise ValueError("synthetic explosion")

    tok = _ExplodingTokenizer(
        eos_token="<EOS_TOKEN>", eos_token_id=99, vocab={"x": 1}
    )
    assert _resolve_eos_token(tok, "unsloth/Llama-3.2-1B-Instruct-bnb-4bit") == snapshot


def test_resolve_eos_token_id_lookup_returns_unknown_token(snapshot):
    """If convert_ids_to_tokens returns a string that isn't in vocab, skip it
    and fall through to per-family fallback."""
    tok = _FakeTokenizer(
        eos_token="<EOS_TOKEN>",
        eos_token_id=42,
        vocab={"<|im_end|>": 1},  # the id-lookup token won't be in here
        id_to_token={42: "<not_in_vocab>"},
    )
    assert _resolve_eos_token(tok, "unsloth/Qwen2.5-0.5B-Instruct-bnb-4bit") == snapshot


