"""Unit tests for the RTX-3060-tuned training/HPO config.

Covers:
  • `LoRAConfig` / `ManualTrainingConfig` / `HPOConfig` bounds and defaults
    (the 3060 calibration — if someone bumps the upper bound back to where
    a 100-trial HPO would silently accept, these guard against regressions).
  • `default_3060_search_space()` produces a valid `HPOSearchSpace`.
  • `_max_safe_batch_for_3060` returns the documented ceiling at each
    (params, seq_len) bucket boundary.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas.training import (
    HPOConfig,
    HPOSearchSpace,
    LoRAConfig,
    ManualTrainingConfig,
)


# ---- LoRAConfig ------------------------------------------------------------


def test_lora_config_defaults_target_all_seven_linear() -> None:
    """QLoRA paper finding: attach to all linear, not just q/k/v/o."""
    cfg = LoRAConfig()
    assert set(cfg.target_modules) == {
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    }


def test_lora_config_rank_upper_bound_is_128_not_256() -> None:
    """3060 calibration — r=256 is unreasonable on 12GB VRAM."""
    LoRAConfig(r=128)  # boundary OK
    with pytest.raises(ValidationError):
        LoRAConfig(r=129)


def test_lora_config_alpha_upper_bound_is_256_not_512() -> None:
    LoRAConfig(alpha=256)
    with pytest.raises(ValidationError):
        LoRAConfig(alpha=257)


# ---- ManualTrainingConfig --------------------------------------------------


def test_manual_config_grad_accum_default_is_8() -> None:
    """Effective batch = batch(2) * accum(8) = 16, the 3060-safe sweet spot."""
    assert ManualTrainingConfig().gradient_accumulation_steps == 8


def test_manual_config_batch_upper_bound_is_16_not_64() -> None:
    ManualTrainingConfig(per_device_train_batch_size=16)
    with pytest.raises(ValidationError):
        ManualTrainingConfig(per_device_train_batch_size=17)


def test_manual_config_grad_accum_upper_bound_is_32() -> None:
    ManualTrainingConfig(gradient_accumulation_steps=32)
    with pytest.raises(ValidationError):
        ManualTrainingConfig(gradient_accumulation_steps=33)


def test_manual_config_has_new_optim_field_with_safe_default() -> None:
    cfg = ManualTrainingConfig()
    assert cfg.optim == "adamw_8bit"


def test_manual_config_optim_rejects_unknown_choice() -> None:
    with pytest.raises(ValidationError):
        ManualTrainingConfig(optim="sgd")


def test_manual_config_optim_accepts_paged_and_torch() -> None:
    ManualTrainingConfig(optim="paged_adamw_8bit")
    ManualTrainingConfig(optim="adamw_torch")


def test_manual_config_packing_defaults_false() -> None:
    assert ManualTrainingConfig().packing is False


def test_manual_config_neftune_defaults_none_and_bounded() -> None:
    assert ManualTrainingConfig().neftune_noise_alpha is None
    ManualTrainingConfig(neftune_noise_alpha=5.0)
    ManualTrainingConfig(neftune_noise_alpha=15.0)
    with pytest.raises(ValidationError):
        ManualTrainingConfig(neftune_noise_alpha=15.1)
    with pytest.raises(ValidationError):
        ManualTrainingConfig(neftune_noise_alpha=-0.1)


# ---- HPOConfig -------------------------------------------------------------


def _minimal_search_space() -> HPOSearchSpace:
    return HPOSearchSpace.model_validate(
        {"learning_rate": {"type": "float", "low": 1e-5, "high": 1e-3, "log": True}}
    )


def test_hpo_config_n_trials_default_is_6() -> None:
    cfg = HPOConfig(search_space=_minimal_search_space())
    assert cfg.n_trials == 6


def test_hpo_config_n_trials_upper_bound_is_20_not_100() -> None:
    """3060 calibration — 100 trials × 3B = ~12 days. Cap at 20."""
    HPOConfig(n_trials=20, search_space=_minimal_search_space())
    with pytest.raises(ValidationError):
        HPOConfig(n_trials=21, search_space=_minimal_search_space())


def test_hpo_config_timeout_seconds_default_is_4_hours() -> None:
    cfg = HPOConfig(search_space=_minimal_search_space())
    assert cfg.timeout_seconds == 14400


def test_hpo_config_timeout_seconds_accepts_none_for_opt_out() -> None:
    cfg = HPOConfig(timeout_seconds=None, search_space=_minimal_search_space())
    assert cfg.timeout_seconds is None


# ---- default_3060_search_space() ------------------------------------------


def test_default_3060_search_space_is_valid_hpo_searchspace() -> None:
    from ai_engine.hpo.search_spaces import default_3060_search_space

    sp = default_3060_search_space()
    assert isinstance(sp, HPOSearchSpace)
    # The 5 tunables we documented:
    assert sp.learning_rate is not None
    assert sp.lora_r is not None
    assert sp.lora_alpha is not None
    assert sp.num_train_epochs is not None
    assert sp.gradient_accumulation_steps is not None
    # The deliberately-not-tuned ones:
    assert sp.per_device_train_batch_size is None
    assert sp.lora_dropout is None
    assert sp.weight_decay is None
    assert sp.warmup_ratio is None
    assert sp.lr_scheduler_type is None


def test_default_3060_search_space_learning_rate_is_log_scale() -> None:
    from ai_engine.hpo.search_spaces import default_3060_search_space

    sp = default_3060_search_space()
    assert sp.learning_rate is not None
    assert sp.learning_rate.log is True
    assert sp.learning_rate.low == pytest.approx(1e-5)
    assert sp.learning_rate.high == pytest.approx(5e-4)


def test_default_3060_search_space_usable_with_hpo_config() -> None:
    """The preset must drop cleanly into an HPOConfig (the obvious FE flow)."""
    from ai_engine.hpo.search_spaces import default_3060_search_space

    cfg = HPOConfig(search_space=default_3060_search_space())
    assert cfg.n_trials == 6


# ---- _max_safe_batch_for_3060 ---------------------------------------------


@pytest.mark.parametrize(
    "params_b, seq_len, expected",
    [
        # ≤1B bucket (TinyLlama-1.1B, Llama-3.2-1B, Qwen3-0.6B, Qwen2.5-0.5B)
        (0.5, 1024, 16),
        (1.1, 2048, 8),
        (1.1, 4096, 4),
        # ≤1.5B bucket (Qwen2.5-1.5B); upper boundary kept for bucket-edge regression
        (1.5, 2048, 4),
        (1.78, 4096, 2),
        # ≤2B bucket (SmolLM2-1.7B, Qwen3-1.7B, Gemma2-2B)
        (1.7, 2048, 4),
        (2.0, 4096, 2),
        # ≤3B bucket (Llama-3.2-3B, Qwen2.5-3B)
        (3.0, 1024, 4),
        (3.0, 2048, 2),
        (3.0, 4096, 1),
        # Boundary: seq exceeds longest row → clamp to last row's ceiling.
        (3.0, 16384, 1),
    ],
)
def test_max_safe_batch_lookup_matches_documented_ceiling(
    params_b: float, seq_len: int, expected: int
) -> None:
    from api.services.training_service import _max_safe_batch_for_3060

    assert _max_safe_batch_for_3060(params_b, seq_len) == expected
