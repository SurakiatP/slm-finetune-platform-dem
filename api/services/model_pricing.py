"""OpenRouter model pricing lookup + cost computation.

Pure pricing arithmetic — deliberately has no notion of a DB row, a
project, or a caller. `usage_service.record()` is the only consumer that
turns a `(model, prompt_tokens, completion_tokens)` triple into a stored
`UsageEvent.cost_usd`.

Imports allowed here: stdlib and `api.core.config` only. **Never**
`api.core.auth` or `api.services.ownership`: those pull in PyJWT at import
time, the GPU worker image ships no PyJWT, and this module is imported by
the Celery worker's write path (`usage_service.record`) at task-module
import time — not lazily, not behind a request. An eager `import jwt`
reachable from here would crash-loop every worker on boot with
`ModuleNotFoundError: No module named 'jwt'`, exactly the failure mode
documented on `api/services/audit_service.py` and guarded by
`tests/unit/test_worker_import_surface.py`. There is no `TYPE_CHECKING`
escape hatch needed in this file specifically (it has no reason to
reference `CurrentUser`), but the constraint is absolute: nothing in this
module may import the auth stack, directly or transitively.
"""

from __future__ import annotations

import json
import logging
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache

from api.core.config import get_settings

logger = logging.getLogger(__name__)

# Model ids actually used by the SDG pipeline today — see
# ai_engine/data_gen/models.py (GENERATOR, JUDGE, DIVERSITY_RULES,
# FORMAT_DETECTION, PDF_QA). FORMAT_DETECTION and PDF_QA are the same SKU
# (google/gemini-2.5-flash-lite, chosen there because it accepts PDF input
# natively); GENERATOR, JUDGE, and DIVERSITY_RULES are also the same SKU
# (deepseek/deepseek-v4-flash-0731). That's why this map has only two
# entries even though five named constants exist upstream.
#
# Values are USD per 1,000,000 tokens — OpenRouter's own published unit —
# so each row can be checked directly against https://openrouter.ai/models
# without a mental unit conversion. `price_for()` below converts to
# per-token before handing prices to callers.
_BUILTIN_PRICING_USD_PER_1M: dict[str, tuple[float, float]] = {
    # google/gemini-2.5-flash-lite — FORMAT_DETECTION, PDF_QA.
    # Published list price at time of writing: $0.10 prompt / $0.40
    # completion per 1M tokens. NEEDS CONFIRMING before this is relied on
    # for real budget enforcement — OpenRouter prices move; re-check
    # https://openrouter.ai/google/gemini-2.5-flash-lite before trusting it
    # for anything beyond development.
    "google/gemini-2.5-flash-lite": (0.10, 0.40),
    # deepseek/deepseek-v4-flash-0731 — GENERATOR, JUDGE, DIVERSITY_RULES.
    # NEEDS CONFIRMING, more urgently than the row above: this exact
    # date-stamped model id was not in the reference pricing data available
    # when this map was written. The value below ($0.14 prompt / $0.28
    # completion per 1M) is a defensible placeholder extrapolated from
    # DeepSeek's other "flash"-tier OpenRouter listings, not a verified
    # quote for this specific SKU — do not treat it as authoritative. Check
    # https://openrouter.ai/deepseek/deepseek-v4-flash-0731 and correct
    # this entry (or set MODEL_PRICING_JSON) before it gates real spend.
    "deepseek/deepseek-v4-flash-0731": (0.14, 0.28),
}


@lru_cache(maxsize=16)
def _merged_pricing_usd_per_1m(overrides_json: str) -> dict[str, tuple[float, float]]:
    """Built-in map + `MODEL_PRICING_JSON` overrides, parsed once per
    distinct override string.

    Caching on the override *string* itself (not "the settings object")
    means a test (or a deployment) that changes `MODEL_PRICING_JSON` gets a
    correctly fresh parse for free — a different string is simply a
    different cache key — without this module needing its own
    cache-invalidation hook to stay in sync with
    `get_settings.cache_clear()`.

    Malformed JSON (parse failure, non-object top level, or a malformed
    per-model entry) is logged and skipped — never raised. A typo in an
    env var must not take down every process that imports this module.
    """
    merged = dict(_BUILTIN_PRICING_USD_PER_1M)
    if not overrides_json:
        return merged

    try:
        parsed = json.loads(overrides_json)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("MODEL_PRICING_JSON is malformed JSON, ignoring override: %s", exc)
        return merged

    if not isinstance(parsed, dict):
        logger.warning(
            "MODEL_PRICING_JSON must be a JSON object of {model_id: {...}}, "
            "got %s, ignoring override",
            type(parsed).__name__,
        )
        return merged

    for model_id, entry in parsed.items():
        try:
            merged[model_id] = (float(entry["prompt"]), float(entry["completion"]))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "MODEL_PRICING_JSON entry for %r is malformed, skipping it: %s",
                model_id,
                exc,
            )
    return merged


def price_for(model: str) -> tuple[float, float] | None:
    """(usd_per_prompt_token, usd_per_completion_token) for `model`.

    `None` when `model` is in neither the built-in map nor the
    `MODEL_PRICING_JSON` override — the caller (`cost_usd` below) is what
    turns that into a NULL `cost_usd` rather than a wrong number.
    """
    settings = get_settings()
    per_1m = _merged_pricing_usd_per_1m(settings.model_pricing_json).get(model)
    if per_1m is None:
        return None
    prompt_per_1m, completion_per_1m = per_1m
    return (prompt_per_1m / 1_000_000, completion_per_1m / 1_000_000)


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal | None:
    """USD cost of this many tokens against `model`'s price, or `None`.

    Returns `None` — never `Decimal("0")` — when `model` is unpriced. A
    model absent from the map still gets its tokens recorded (the caller
    stamps them onto the `UsageEvent` row regardless), but `cost_usd` is
    left NULL rather than 0: 0 is a lie that lets a budget cap be bypassed
    — a run against an unpriced model would look free and sail straight
    past `usage_service.assert_within_budget` forever.
    """
    price = price_for(model)
    if price is None:
        return None
    prompt_price, completion_price = price
    total = (
        Decimal(str(prompt_price)) * prompt_tokens
        + Decimal(str(completion_price)) * completion_tokens
    )
    # Quantized to match `usage_events.cost_usd`'s `Numeric(12, 6)` column
    # and to absorb the float->Decimal noise introduced by the /1_000_000
    # division above (e.g. 0.10/1e6 is not exactly representable in binary
    # floating point).
    return total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


__all__ = ["cost_usd", "price_for"]
