"""Validation rules for the Deployment and ApiKey request/response schemas
(T2 of the deployments+api-keys plan). Pure pydantic checks — no DB/service
wiring here, that's T3/T5's job.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from api.schemas.api_keys import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyResponse
from api.schemas.deployments import DeploymentCreate, DeploymentResponse, DeploymentUpdate
from api.schemas.enums import JobStatus

_ARTIFACT_ID = uuid4()


# ---- DeploymentCreate -------------------------------------------------------


def test_deployment_create_name_optional():
    body = DeploymentCreate(model_artifact_id=_ARTIFACT_ID)
    assert body.name is None


def test_deployment_create_requires_model_artifact_id():
    with pytest.raises(ValidationError):
        DeploymentCreate()  # type: ignore[call-arg]


def test_deployment_create_rejects_empty_name():
    with pytest.raises(ValidationError):
        DeploymentCreate(model_artifact_id=_ARTIFACT_ID, name="")


def test_deployment_create_rejects_name_over_200_chars():
    with pytest.raises(ValidationError):
        DeploymentCreate(model_artifact_id=_ARTIFACT_ID, name="x" * 201)


def test_deployment_create_rejects_unknown_field():
    with pytest.raises(ValidationError):
        DeploymentCreate(model_artifact_id=_ARTIFACT_ID, extra_field="nope")


# ---- DeploymentUpdate -------------------------------------------------------


def test_deployment_update_all_fields_optional():
    body = DeploymentUpdate()
    assert body.name is None
    assert body.rate_limit_per_min is None


def test_deployment_update_rejects_rate_limit_below_1():
    with pytest.raises(ValidationError):
        DeploymentUpdate(rate_limit_per_min=0)


def test_deployment_update_accepts_rate_limit_above_default_cap():
    """No cap is enforced in the schema itself — the 600 ceiling is a
    service-level 422 (settings.deployment_max_rate_limit_per_min), not a
    pydantic constraint, since the cap is configurable at runtime."""
    body = DeploymentUpdate(rate_limit_per_min=10_000)
    assert body.rate_limit_per_min == 10_000


# ---- DeploymentResponse -----------------------------------------------------


def test_deployment_response_round_trip():
    now = datetime.now(UTC)
    resp = DeploymentResponse(
        id=uuid4(),
        name="my-deployment",
        model_artifact_id=_ARTIFACT_ID,
        model_tag="my-model:latest",
        status=JobStatus.RUNNING,
        rate_limit_per_min=60,
        job_id=str(uuid4()),
        error_message=None,
        created_at=now,
        updated_at=now,
    )
    assert resp.status is JobStatus.RUNNING


def test_deployment_response_allows_null_model_artifact_id():
    """ondelete=SET NULL on model_artifact_id means a response for a
    deployment whose artifact was deleted must still validate."""
    now = datetime.now(UTC)
    resp = DeploymentResponse(
        id=uuid4(),
        name="orphaned",
        model_artifact_id=None,
        model_tag=None,
        status=JobStatus.COMPLETED,
        rate_limit_per_min=60,
        job_id=None,
        error_message=None,
        created_at=now,
        updated_at=now,
    )
    assert resp.model_artifact_id is None


# ---- ApiKeyCreate ------------------------------------------------------------


def test_api_key_create_requires_name():
    with pytest.raises(ValidationError):
        ApiKeyCreate()  # type: ignore[call-arg]


def test_api_key_create_rejects_empty_name():
    with pytest.raises(ValidationError):
        ApiKeyCreate(name="")


def test_api_key_create_rejects_unknown_field():
    with pytest.raises(ValidationError):
        ApiKeyCreate(name="ci key", scopes=["read"])


# ---- ApiKeyResponse / ApiKeyCreatedResponse -----------------------------------


def _key_kwargs(**overrides):
    now = datetime.now(UTC)
    base = dict(
        id=uuid4(),
        name="ci key",
        prefix="sk-slm-abc",
        last4="wxyz",
        status="active",
        created_at=now,
        last_used_at=None,
    )
    base.update(overrides)
    return base


def test_api_key_response_status_rejects_arbitrary_string():
    with pytest.raises(ValidationError):
        ApiKeyResponse(**_key_kwargs(status="deleted"))


def test_api_key_response_accepts_revoked_status():
    resp = ApiKeyResponse(**_key_kwargs(status="revoked"))
    assert resp.status == "revoked"


def test_api_key_created_response_carries_plaintext_key():
    resp = ApiKeyCreatedResponse(**_key_kwargs(), key="sk-slm-plaintext-value")
    assert resp.key == "sk-slm-plaintext-value"
    assert resp.status == "active"
