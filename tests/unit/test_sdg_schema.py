"""Validate `holdout_size` / `holdout_name` fields on SDGRequest (defaults, bounds)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from api.schemas.sdg import SDGRequest


_PROJECT_ID = str(uuid4())
_SEED_ID = str(uuid4())
_ADAPTER = TypeAdapter(SDGRequest)


def _qa_with_seed_base() -> dict:
    return {
        "sdg_mode": "with_seed",
        "project_id": _PROJECT_ID,
        "task_type": "qa",
        "task_description": "Answer policy questions",
        "num_samples": 50,
        "seed_dataset_id": _SEED_ID,
    }


def test_holdout_size_defaults_to_100_when_omitted():
    req = _ADAPTER.validate_python(_qa_with_seed_base())
    assert req.holdout_size == 100


def test_holdout_size_zero_is_valid():
    payload = _qa_with_seed_base() | {"holdout_size": 0}
    req = _ADAPTER.validate_python(payload)
    assert req.holdout_size == 0


def test_holdout_size_explicit_value():
    payload = _qa_with_seed_base() | {"holdout_size": 25}
    req = _ADAPTER.validate_python(payload)
    assert req.holdout_size == 25


def test_holdout_size_negative_rejected():
    payload = _qa_with_seed_base() | {"holdout_size": -1}
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(payload)


def test_holdout_size_over_cap_rejected():
    payload = _qa_with_seed_base() | {"holdout_size": 2001}
    with pytest.raises(ValidationError):
        _ADAPTER.validate_python(payload)


def test_holdout_size_present_on_description_only_mode():
    payload = {
        "sdg_mode": "description_only",
        "project_id": _PROJECT_ID,
        "task_type": "classification",
        "task_description": "Classify support tickets",
        "num_samples": 200,
        "holdout_size": 50,
        "classification_config": {"labels": ["billing", "technical"]},
    }
    req = _ADAPTER.validate_python(payload)
    assert req.holdout_size == 50


def test_holdout_name_defaults_to_none_when_omitted():
    req = _ADAPTER.validate_python(_qa_with_seed_base())
    assert req.holdout_name is None


def test_holdout_name_accepts_explicit_value():
    payload = _qa_with_seed_base() | {"holdout_name": "my-custom-holdout"}
    req = _ADAPTER.validate_python(payload)
    assert req.holdout_name == "my-custom-holdout"


def test_holdout_name_present_on_description_only_mode():
    payload = {
        "sdg_mode": "description_only",
        "project_id": _PROJECT_ID,
        "task_type": "classification",
        "task_description": "Classify support tickets",
        "num_samples": 200,
        "holdout_size": 50,
        "holdout_name": "support-tickets-holdout",
        "classification_config": {"labels": ["billing", "technical"]},
    }
    req = _ADAPTER.validate_python(payload)
    assert req.holdout_name == "support-tickets-holdout"
