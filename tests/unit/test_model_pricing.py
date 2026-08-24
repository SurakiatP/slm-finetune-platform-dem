"""Unit tests for `api/services/model_pricing.py`.

Layers covered:
  1. `price_for` — known/unknown models, per-token conversion.
  2. `cost_usd` — arithmetically correct `Decimal` for a known model, `None`
     (never `Decimal("0")`) for an unknown one.
  3. `MODEL_PRICING_JSON` override — wins over the built-in map, and
     malformed JSON is ignored rather than fatal.
  4. Import-surface — this module must import cleanly with `jwt` blocked at
     the meta-path, the same technique `tests/unit/test_worker_import_surface.py`
     uses for the worker-boot modules, since `usage_service` (a worker-boot
     dependency) imports this module.

No DB needed anywhere in this file — `model_pricing` never touches one.
"""

from __future__ import annotations

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from api.core.config import get_settings
from api.services import model_pricing

_REPO_ROOT = Path(__file__).resolve().parents[2]

_KNOWN_MODEL = "google/gemini-2.5-flash-lite"
_UNKNOWN_MODEL = "some-vendor/does-not-exist"


@pytest.fixture(autouse=True)
def _reset_pricing_env(monkeypatch: pytest.MonkeyPatch):
    """Every test starts from the built-in map with no override, and
    leaves `get_settings()` clean for whichever test runs next.
    """
    monkeypatch.setenv("MODEL_PRICING_JSON", "")
    get_settings.cache_clear()
    yield
    monkeypatch.setenv("MODEL_PRICING_JSON", "")
    get_settings.cache_clear()


class TestPriceFor:
    def test_known_model_returns_per_token_floats(self) -> None:
        price = model_pricing.price_for(_KNOWN_MODEL)
        assert price is not None
        prompt_per_token, completion_per_token = price
        # Built-in map: $0.10 / $0.40 per 1M tokens.
        assert prompt_per_token == pytest.approx(0.10 / 1_000_000)
        assert completion_per_token == pytest.approx(0.40 / 1_000_000)

    def test_unknown_model_returns_none(self) -> None:
        assert model_pricing.price_for(_UNKNOWN_MODEL) is None


class TestCostUsd:
    def test_known_model_is_arithmetically_correct(self) -> None:
        # Round token counts (1M each) so the $/1M list price divides out
        # exactly, independent of the float->Decimal noise `cost_usd`
        # quantizes away.
        cost = model_pricing.cost_usd(_KNOWN_MODEL, 1_000_000, 1_000_000)
        assert cost == Decimal("0.500000")

    def test_known_model_scales_linearly(self) -> None:
        cost = model_pricing.cost_usd(_KNOWN_MODEL, 2_000_000, 0)
        assert cost == Decimal("0.200000")

    def test_unknown_model_returns_none_not_zero(self) -> None:
        cost = model_pricing.cost_usd(_UNKNOWN_MODEL, 1_000, 1_000)
        assert cost is None
        assert cost != Decimal("0")

    def test_zero_tokens_known_model_is_zero_not_none(self) -> None:
        # A priced model always yields a real Decimal, even $0 — only an
        # *unpriced* model yields None.
        cost = model_pricing.cost_usd(_KNOWN_MODEL, 0, 0)
        assert cost == Decimal("0.000000")


class TestEmbeddingModelPricing:
    """openai/text-embedding-3-small — SDG semantic dedup (see
    ai_engine/data_gen/semantic_dedup.py). $0.02 / 1M input tokens,
    completion is 0.0 since embeddings return no completion tokens."""

    _EMBEDDING_MODEL = "openai/text-embedding-3-small"

    def test_price_for_embedding_model(self) -> None:
        price = model_pricing.price_for(self._EMBEDDING_MODEL)
        assert price == (0.02 / 1_000_000, 0.0)

    def test_cost_usd_for_embedding_model(self) -> None:
        cost = model_pricing.cost_usd(self._EMBEDDING_MODEL, 1_000_000, 0)
        assert cost == Decimal("0.020000")


class TestModelPricingJsonOverride:
    def test_override_wins_for_a_new_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "MODEL_PRICING_JSON",
            '{"vendor/new-model": {"prompt": 1.0, "completion": 2.0}}',
        )
        get_settings.cache_clear()

        price = model_pricing.price_for("vendor/new-model")
        assert price is not None
        assert price[0] == pytest.approx(1.0 / 1_000_000)
        assert price[1] == pytest.approx(2.0 / 1_000_000)

    def test_override_replaces_a_builtin_price(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "MODEL_PRICING_JSON",
            f'{{"{_KNOWN_MODEL}": {{"prompt": 5.0, "completion": 5.0}}}}',
        )
        get_settings.cache_clear()

        cost = model_pricing.cost_usd(_KNOWN_MODEL, 1_000_000, 1_000_000)
        assert cost == Decimal("10.000000")

    def test_builtin_models_untouched_when_override_adds_unrelated_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "MODEL_PRICING_JSON",
            '{"vendor/new-model": {"prompt": 1.0, "completion": 2.0}}',
        )
        get_settings.cache_clear()

        # The override is a merge, not a replace of the whole map.
        price = model_pricing.price_for(_KNOWN_MODEL)
        assert price is not None
        assert price[0] == pytest.approx(0.10 / 1_000_000)

    def test_malformed_json_is_ignored_not_fatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MODEL_PRICING_JSON", "{not valid json")
        get_settings.cache_clear()

        # Must not raise, and must fall back to the built-in map.
        price = model_pricing.price_for(_KNOWN_MODEL)
        assert price is not None
        assert price[0] == pytest.approx(0.10 / 1_000_000)

    def test_non_object_json_is_ignored_not_fatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MODEL_PRICING_JSON", '["not", "an", "object"]')
        get_settings.cache_clear()

        price = model_pricing.price_for(_KNOWN_MODEL)
        assert price is not None

    def test_malformed_entry_is_skipped_others_still_applied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "MODEL_PRICING_JSON",
            '{"vendor/bad": {"prompt": "not-a-number"}, '
            '"vendor/good": {"prompt": 3.0, "completion": 4.0}}',
        )
        get_settings.cache_clear()

        assert model_pricing.price_for("vendor/bad") is None
        good = model_pricing.price_for("vendor/good")
        assert good is not None
        assert good[0] == pytest.approx(3.0 / 1_000_000)


# =============================================================================
# Import-surface: must not pull in PyJWT (see module header + the risk this
# guards against on tests/unit/test_worker_import_surface.py).
# =============================================================================

_BLOCK_JWT_AND_IMPORT = """
import sys, importlib.abc


class _NoPyJWT(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "jwt" or name.startswith("jwt."):
            raise ModuleNotFoundError(
                "No module named 'jwt' (simulating the worker image)"
            )
        return None


sys.meta_path.insert(0, _NoPyJWT())
import {module}
print("IMPORTED")
"""


def _import_without_pyjwt(module: str) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        [sys.executable, "-c", _BLOCK_JWT_AND_IMPORT.format(module=module)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **dict(os.environ),
            "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_unused",
        },
    )


def test_model_pricing_is_importable_without_pyjwt() -> None:
    result = _import_without_pyjwt("api.services.model_pricing")
    assert "IMPORTED" in result.stdout, result.stderr
