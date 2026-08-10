"""The frontend handoff docs must describe endpoints that actually exist.

`docs/patches/smart-model-tune-unused-endpoints.md` tells an external team
"call this, it is already built". A path that is renamed, or a field that is
dropped, turns that promise into a 404 on their side — and they cannot tell
whether the doc is stale or their build is old. Round 3.5's review found
exactly this failure already once (finding C1-1: the handoff doc quoted
`docs/04` as saying the *inverse* of that file's current text, and the
mistake survived because nothing checked the doc against the source).

So: every `/api/v1/...` path the doc cites is enumerated out of the prose and
checked against `openapi.json`, which is the generated contract. Same for
the response field names the doc lists for the schemas it documents in
detail — those are what the team will write TypeScript interfaces from.

Deliberately NOT a substring check over the whole file: the recurring defect
in this repo is "one of the N call sites was missed", and a whole-file
`in` check goes green the moment any one citation is right. Every assertion
below enumerates and checks each item, with a non-vacuity backstop first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PATCHES = _REPO / "docs" / "patches"
_DOC = _PATCHES / "smart-model-tune-unused-endpoints.md"
_SPEC = _REPO / "openapi.json"

# Every handoff doc that cites live API routes. `smart-model-tune-HANDOFF.md`
# is the cover document actually sent to the external team, and it tells them
# in writing that a guard test keeps these files true — so it has to be in
# here, or that sentence is a lie. `smart-model-tune-auth.md` is deliberately
# NOT listed: it documents a future `AUTH_REQUIRED=true` world and cites
# frontend source lines, not Engine routes.
_ROUTE_CITING_DOCS = [_DOC, _PATCHES / "smart-model-tune-HANDOFF.md"]

# Path parameters are spelled with the real placeholder in the spec
# (`{dataset_id}`) but sometimes generically in prose (`{id}`). Normalise
# both sides to `{}` so the comparison is about the ROUTE, not the parameter
# name — a renamed path parameter is not a broken instruction to the reader.
_PARAM = re.compile(r"\{[^}]*\}")


def _normalise(path: str) -> str:
    return _PARAM.sub("{}", path.rstrip("/"))


def _spec_paths() -> set[str]:
    spec = json.loads(_SPEC.read_text(encoding="utf-8"))
    paths = spec.get("paths") or {}
    assert paths, (
        "openapi.json has no paths — the spec is broken or unreadable; do not "
        "delete this assertion, every check below would pass vacuously"
    )
    return {_normalise(p) for p in paths}


def _doc_text() -> str:
    assert _DOC.exists(), f"{_DOC.name} is gone — delete this test file too, deliberately"
    return _DOC.read_text(encoding="utf-8")


def _routes_in(path: Path) -> list[str]:
    """Every `/api/v1/...` route mentioned anywhere in one doc.

    Trailing punctuation and markdown table pipes are stripped; query
    strings are dropped (`?project_id=` is not part of the route).
    """
    assert path.exists(), (
        f"{path.name} is listed in _ROUTE_CITING_DOCS but does not exist — "
        "either it was deleted (remove it from the list deliberately) or "
        "renamed; a missing file must not silently drop its guards"
    )
    raw = re.findall(r"/api/v1/[A-Za-z0-9_{}/-]*", path.read_text(encoding="utf-8"))
    cited = []
    for hit in raw:
        route = hit.split("?")[0].rstrip("/.,`|")
        if route and route != "/api/v1":
            cited.append(route)
    assert cited, (
        f"no /api/v1 routes found in {path.name} — either the doc was "
        "rewritten into a different shape or this parser broke; an "
        "enumeration over nothing passes vacuously"
    )
    return sorted(set(cited))


def _cited_paths() -> list[tuple[str, str]]:
    """(doc name, route) for every route cited across all handoff docs."""
    pairs = [(p.name, r) for p in _ROUTE_CITING_DOCS for r in _routes_in(p)]
    assert len({name for name, _ in pairs}) == len(_ROUTE_CITING_DOCS), (
        "at least one handoff doc contributed no routes — see _routes_in's "
        "own backstop; this cross-check catches a doc silently dropping out"
    )
    return sorted(set(pairs))


@pytest.mark.parametrize("doc_name,route", _cited_paths())
def test_every_endpoint_the_handoff_docs_cite_exists(doc_name: str, route: str) -> None:
    """One test per (doc, route), so a failure names the exact broken line."""
    assert _normalise(route) in _spec_paths(), (
        f"{doc_name} tells the smart-model-tune team to call `{route}`, "
        f"which is not in openapi.json. Either the route was renamed (fix the "
        f"doc — an external team is reading it as fact) or openapi.json is "
        f"stale (regenerate it with scripts/export_openapi.py)."
    )


def _schema_properties(name: str) -> set[str]:
    spec = json.loads(_SPEC.read_text(encoding="utf-8"))
    schema = (spec.get("components", {}).get("schemas") or {}).get(name)
    assert schema is not None, (
        f"schema {name} is missing from openapi.json — the handoff doc "
        f"documents its fields, so the team would be writing an interface for "
        f"a type that no longer exists"
    )
    props = set(schema.get("properties") or {})
    assert props, f"schema {name} has no properties — parser or spec is broken"
    return props


# The schemas whose field lists the doc spells out verbatim. If the doc grows
# another such list, add it here — that is the point of keeping the map
# explicit rather than scraping every capitalised word.
_DOCUMENTED_SCHEMAS = {
    "UsageSummaryResponse": [
        "period_start",
        "period_end",
        "prompt_tokens",
        "completion_tokens",
        "cost_usd",
        "has_unpriced_usage",
    ],
    "UsageEventResponse": [
        "provider",
        "model",
        "stage",
        "prompt_tokens",
        "completion_tokens",
        "cost_usd",
        "outcome",
    ],
    "DatasetDownloadUrlResponse": [
        "url",
        "filename",
        "content_type",
        "expires_at",
        "expires_in",
    ],
    "ModelDownloadUrlResponse": ["format", "files", "expires_at", "expires_in", "truncated"],
    "EvaluationAcceptedResponse": ["evaluation_id", "job_id", "status", "websocket_url"],
    "EvaluationResponse": [
        "model_artifact_id",
        "dataset_id",
        "celery_task_id",
        "status",
        "metrics_json",
        "llm_judge_score",
        "llm_judge_model",
    ],
    "BaseModelInfo": [
        "id",
        "display_name",
        "family",
        "params_billions",
        "context_length",
        "recommended_max_seq_length",
        "quantization",
        "license",
        "ollama_tag",
    ],
    "DatasetResponse": ["celery_task_id", "status", "error_message", "storage_uri"],
}


@pytest.mark.parametrize("schema_name,fields", sorted(_DOCUMENTED_SCHEMAS.items()))
def test_documented_response_fields_still_exist(schema_name: str, fields: list[str]) -> None:
    actual = _schema_properties(schema_name)
    missing = [f for f in fields if f not in actual]
    assert not missing, (
        f"the handoff doc lists {missing} on {schema_name}, but openapi.json "
        f"no longer has them. The team writes TypeScript interfaces off this "
        f"doc — a dropped field becomes an undefined at runtime."
    )


def test_evaluation_request_body_matches_what_the_doc_tells_them_to_send() -> None:
    """The doc shows a literal POST body. Its required fields must really be
    required, and its optional ones really optional — sending the documented
    body must be a valid request, not a 422."""
    spec = json.loads(_SPEC.read_text(encoding="utf-8"))
    ref = spec["paths"]["/api/v1/evaluations"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    schema = spec["components"]["schemas"][ref.split("/")[-1]]
    required = set(schema.get("required") or [])
    assert required == {"model_artifact_id", "dataset_id"}, (
        f"POST /evaluations now requires {sorted(required)}; the handoff doc "
        "shows a body with model_artifact_id + dataset_id required and "
        "use_llm_judge/judge_model optional. Following the doc would 422."
    )
    props = set(schema.get("properties") or {})
    for optional in ("use_llm_judge", "judge_model"):
        assert optional in props, (
            f"the doc documents `{optional}` on the evaluation body, which is "
            "no longer accepted"
        )


def test_training_body_still_has_the_hpo_mode_arm() -> None:
    """§2 tells them HPO is one field away — that is only true while
    `POST /trainings` stays a discriminated union with an `hpo` arm."""
    spec = json.loads(_SPEC.read_text(encoding="utf-8"))
    body = spec["paths"]["/api/v1/trainings"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    mapping = (body.get("discriminator") or {}).get("mapping") or {}
    assert set(mapping) == {"manual", "hpo"}, (
        f"POST /trainings discriminator is now {sorted(mapping)}; the handoff "
        "doc tells the team `mode: \"hpo\"` works on the same endpoint"
    )


def test_job_progress_union_covers_every_frame_the_doc_names() -> None:
    """§5 lists the union members by name so they can type the response."""
    spec = json.loads(_SPEC.read_text(encoding="utf-8"))
    schema = spec["paths"]["/api/v1/jobs/{job_id}/progress"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    members = {r.get("$ref", "").split("/")[-1] for r in schema.get("oneOf", [])}
    assert members, "the job-progress response is no longer a union — parser or contract changed"
    doc = _doc_text()
    for name in sorted(members):
        assert name in doc, (
            f"the job-progress union gained `{name}`, which the handoff doc's "
            "§5 frame list does not mention — the team would not know to "
            "handle it"
        )


@pytest.mark.parametrize("doc", _ROUTE_CITING_DOCS, ids=lambda p: p.name)
def test_doc_records_the_backend_commit_it_describes(doc: Path) -> None:
    """A handoff doc with no version is unfalsifiable: the reader cannot tell
    whether a mismatch means their build is old or the doc is."""
    assert re.search(r"dev @ [0-9a-f]{7,40}", doc.read_text(encoding="utf-8")), (
        f"{doc.name} no longer states the backend commit it describes"
    )


def test_handoff_cover_doc_points_at_the_detail_doc() -> None:
    """The cover doc is what gets sent; if its pointer to the endpoint
    inventory rots, the team never finds the 27 unwired routes."""
    cover = (_PATCHES / "smart-model-tune-HANDOFF.md").read_text(encoding="utf-8")
    assert _DOC.name in cover, (
        "the handoff cover no longer references "
        f"{_DOC.name} — that link is the whole point of the cover doc"
    )
    assert "smart-model-tune-auth.md" in cover, (
        "the handoff cover no longer points at the auth patch, which the team "
        "needs the moment AUTH_REQUIRED flips"
    )
    for referenced in (_DOC, _PATCHES / "smart-model-tune-auth.md"):
        assert referenced.exists(), (
            f"the handoff cover sends the team to {referenced.name}, which "
            "does not exist"
        )
