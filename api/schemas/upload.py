"""Schemas attached to seed-upload responses (Phase 9).

`FormatDetectionReport` is the audit trail of one Format Detection pass —
stored both in the upload response body and in
`Dataset.generation_metadata['format_detection']` so the SDG worker can
inspect it later.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FormatDetectionReport(BaseModel):
    """Audit trail of one Format Detection pass.

    Persisted on Dataset.generation_metadata['format_detection'] AND
    returned in the upload-seed response body.
    """

    model_config = ConfigDict(extra="forbid")

    ran: bool = Field(
        ...,
        description=(
            "True if the LLM was actually invoked. False when the seed was "
            "already canonical (no rename needed) or for PDF uploads."
        ),
    )
    model_used: str | None = Field(
        default=None,
        description="Model identifier the Format Detection LLM call used (or None if skipped).",
    )
    field_mapping: dict[str, str] = Field(
        default_factory=dict,
        description='Old-key → canonical-key map produced by the LLM. e.g. {"text1": "text"}.',
    )
    rows_total: int = Field(..., ge=0, description="Total candidate rows considered.")
    rows_canonicalised: int = Field(
        ...,
        ge=0,
        description="Rows that survived rename + required-key check (may equal rows_total if no rename needed).",
    )
    rows_dropped: int = Field(
        ...,
        ge=0,
        description="Rows dropped because required canonical keys remained missing after rename.",
    )
    notes: str | None = Field(
        default=None,
        description="Free-form note (e.g. 'already canonical', 'llm error: ...').",
    )


__all__ = ["FormatDetectionReport"]
