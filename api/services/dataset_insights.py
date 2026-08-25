"""Application service for `GET /api/v1/datasets/{id}/insights`.

A data-quality scan over a dataset's stored rows: label balance, exact
and near duplicates, sample-length distribution/outliers, and a rolled-up
0-100 quality score with a readiness verdict. Mirrors the frontend's
`frontend-punpun/src/lib/qualityCalculator.ts` (same six length buckets,
same issue ids/severities, same score formula) so the two surfaces agree
whether the frontend computes locally or reads this endpoint.

Row scanning follows `datasets_service.preview_dataset`'s MinIO streaming
pattern exactly (line-split on `\n`, tolerate a trailing un-newlined
record, skip unparseable lines) but caps at `INSIGHTS_SCAN_LIMIT` rows
instead of a small preview `limit` — large datasets get a
statistically-representative prefix rather than a full table scan on
every request, and `scan_truncated=True` signals the response is a
sample rather than the whole dataset.

Two independent duplicate signals are reported:
  * `duplicate_rows` — EXACT duplicates, compared on whitespace-
    normalised, lowercased text (stricter than `qualityCalculator.ts`'s
    raw-trim compare, deliberately: this is the backend's own pass, not
    a byte-for-byte port).
  * `near_duplicate_count` — paraphrase-level duplicates via the same
    MinHash LSH used by the SDG generation loop
    (`ai_engine.data_gen.minhash_dedup.MinHashDeduplicator`), Jaccard
    >= 0.90 over 5-gram shingles.

`judge`/`judge_by_key`/`counts` surface whatever SDG-time aggregates are
already stored on `Dataset.generation_metadata["insights"]` (produced by
`ai_engine.data_gen.insights.JudgeScoreAggregator` at generation time, for
datasets that went through SDG). Uploaded/legacy datasets carry no such
blob, and any malformed/partial shape degrades to `None` for all three
rather than a 500 — this is a read-side rendering of upstream state, not
something worth failing the whole request over.
"""

from __future__ import annotations

import json
import re
import statistics
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.data_gen.minhash_dedup import MinHashDeduplicator
from api.core.auth import CurrentUser
from api.schemas.datasets import (
    DatasetInsightsResponse,
    GenerationCounts,
    InsightIssue,
    InsightLabelCount,
    InsightLengthBucket,
    JudgeDimensionStats,
    JudgeStats,
)
from api.schemas.enums import TaskType
from api.services import ownership
from workers.storage import get_minio_client, parse_s3_uri

# Cap on the number of rows scanned per insights request. `scan_truncated`
# on the response tells the caller whether the dataset actually had more
# rows than this — see `_scan_rows`.
INSIGHTS_SCAN_LIMIT = 5000

# Which JSONL field carries the "text" this scan measures length/duplicates
# over, per task type. Mirrors `datasetExpander.ts`'s `getTextColumn`.
_TEXT_FIELD: dict[TaskType, str] = {
    TaskType.CLASSIFICATION: "text",
    TaskType.TOOL_CALLING: "question",
    TaskType.QA: "question",
}

# Same six buckets as frontend-punpun/src/lib/qualityCalculator.ts. `None`
# upper bound means unbounded ("1200+").
_LENGTH_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0-50", 0, 50),
    ("51-150", 51, 150),
    ("151-300", 151, 300),
    ("301-600", 301, 600),
    ("601-1200", 601, 1200),
    ("1200+", 1201, None),
)

_JUDGE_AXES = ("fidelity", "naturalness", "utility", "weighted")

_WHITESPACE_RE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Whitespace-collapse + strip + lowercase, for exact-duplicate compare."""
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def _extract_label(row: dict, task_type: TaskType) -> str | None:
    """The row's label, or `None` when this task type has no label concept
    (`qa`) or the row's label is missing/blank/unparseable.

    `classification` reads `row["label"]` directly; `tool_calling` parses
    `row["answer"]` (a JSON string, see `api/schemas/data_formats.py`'s
    `ToolCallingSample`) and reads its `name` key, tolerating any parse
    failure as "missing" rather than raising.
    """
    if task_type == TaskType.CLASSIFICATION:
        val = row.get("label")
        return val.strip() if isinstance(val, str) and val.strip() else None
    if task_type == TaskType.TOOL_CALLING:
        raw = row.get("answer")
        if not isinstance(raw, str):
            return None
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        name = obj.get("name") if isinstance(obj, dict) else None
        return name.strip() if isinstance(name, str) and name.strip() else None
    return None  # qa — no label concept


def _scan_rows(storage_uri: str) -> tuple[list[dict], bool]:
    """Stream `storage_uri`'s JSONL, parse up to `INSIGHTS_SCAN_LIMIT` rows.

    Same MinIO streaming shape as `datasets_service.preview_dataset`: parse
    line-by-line off a growing buffer, tolerate a trailing un-newlined
    record, skip lines that don't parse as JSON. Returns
    `(rows, scan_truncated)` — `scan_truncated` is `True` iff the object
    had at least one more (non-blank) line past the cap.
    """
    bucket, key = parse_s3_uri(storage_uri)
    minio = get_minio_client()
    response = minio.get_object(bucket_name=bucket, object_name=key)
    rows: list[dict] = []
    truncated = False
    try:
        buf = b""
        for chunk in response.stream(64 * 1024):
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                if len(rows) >= INSIGHTS_SCAN_LIMIT:
                    truncated = True
                    break
                try:
                    rows.append(json.loads(line.decode("utf-8")))
                except json.JSONDecodeError:
                    continue
            if truncated:
                break
        if not truncated:
            trailing = buf.strip()
            if trailing:
                if len(rows) >= INSIGHTS_SCAN_LIMIT:
                    truncated = True
                else:
                    try:
                        rows.append(json.loads(trailing.decode("utf-8")))
                    except json.JSONDecodeError:
                        pass
    finally:
        response.close()
        response.release_conn()
    return rows, truncated


def _judge_stats_from_dict(d: dict) -> JudgeStats:
    """Map one `JudgeScoreAggregator.to_dict()`-shaped dict to `JudgeStats`.

    Raises (KeyError/TypeError/ValueError) on any missing/malformed field —
    the caller (`_map_stored_insights`) catches that and degrades to `None`
    rather than letting a bad stored blob 500 the request.
    """
    mean = d["mean"]
    histogram = d["histogram"]
    axes = {
        axis: JudgeDimensionStats(
            mean=float(mean[axis]), histogram=list(histogram[axis])
        )
        for axis in _JUDGE_AXES
    }
    return JudgeStats(count=int(d["count"]), **axes)


def _map_stored_insights(
    generation_metadata: dict | None,
) -> tuple[JudgeStats | None, dict[str, JudgeStats] | None, GenerationCounts | None]:
    """Read `generation_metadata["insights"]` (schema: `{"schema_version": 1,
    "judge": {...} | None, "counts": {...}}`, written by
    `ai_engine.data_gen.insights.JudgeScoreAggregator` at SDG time) into
    `(judge, judge_by_key, counts)`.

    Any missing key, wrong type, or absent blob at all (uploaded/legacy
    datasets) degrades the whole triple to `(None, None, None)` — this is
    read-side rendering of upstream state, never a 500.
    """
    try:
        insights = (generation_metadata or {}).get("insights")
        if not isinstance(insights, dict):
            return None, None, None

        judge_raw = insights.get("judge")
        judge = _judge_stats_from_dict(judge_raw) if isinstance(judge_raw, dict) else None

        judge_by_key: dict[str, JudgeStats] | None = None
        if isinstance(judge_raw, dict):
            by_key_raw = judge_raw.get("by_key")
            if isinstance(by_key_raw, dict):
                judge_by_key = {
                    key: _judge_stats_from_dict(val)
                    for key, val in by_key_raw.items()
                    if isinstance(val, dict)
                }

        counts_raw = insights.get("counts")
        counts: GenerationCounts | None = None
        if isinstance(counts_raw, dict):
            counts = GenerationCounts(
                **{field: counts_raw.get(field) for field in GenerationCounts.model_fields}
            )

        return judge, judge_by_key, counts
    except Exception:  # noqa: BLE001 — malformed stored shape degrades to nulls
        return None, None, None


def _build_issues(
    *,
    label_distribution: list[InsightLabelCount],
    total_labeled: int,
    duplicate_rows: int,
    missing_labels: int,
    outliers: int,
    outlier_threshold: float,
    mean_len: float,
    std_len: float,
    num_lengths: int,
) -> list[InsightIssue]:
    """Port of `qualityCalculator.ts`'s five issue generators — same ids,
    same severity thresholds, same suggestion spirit, snake_case fields."""
    issues: list[InsightIssue] = []

    if len(label_distribution) >= 2:
        smallest = label_distribution[-1]
        largest = label_distribution[0]
        if smallest.percent < 5 and total_labeled > 50:
            issues.append(
                InsightIssue(
                    id="iss-imbalance",
                    severity="critical",
                    category="imbalance",
                    title=f"Severe class imbalance — '{smallest.label}' only {smallest.percent}%",
                    description=(
                        f"Class '{smallest.label}' has {smallest.count} samples "
                        f"({smallest.percent}%), while '{largest.label}' has "
                        f"{largest.count} ({largest.percent}%)."
                    ),
                    affected_rows=smallest.count,
                    suggestion=(
                        f"Oversample '{smallest.label}' or collect ~"
                        f"{max(50, round(total_labeled * 0.05) - smallest.count)} "
                        "more samples to reach 5%."
                    ),
                )
            )
        elif smallest.percent < 10 and total_labeled > 50:
            issues.append(
                InsightIssue(
                    id="iss-imbalance",
                    severity="warning",
                    category="imbalance",
                    title=f"Mild class imbalance — '{smallest.label}' at {smallest.percent}%",
                    description=(
                        f"Smallest class has {smallest.count} samples vs largest "
                        f"at {largest.count}."
                    ),
                    affected_rows=smallest.count,
                    suggestion=(
                        "Consider class weighting or moderate oversampling of "
                        f"'{smallest.label}'."
                    ),
                )
            )

    if duplicate_rows > 0:
        issues.append(
            InsightIssue(
                id="iss-duplicates",
                severity="warning" if duplicate_rows > 30 else "info",
                category="duplicates",
                title=f"{duplicate_rows} duplicate rows detected",
                description="Exact text duplicates skew evaluation and cause overfitting.",
                affected_rows=duplicate_rows,
                suggestion=(
                    "Remove duplicates before training — saves training time "
                    "and improves eval validity."
                ),
            )
        )

    if missing_labels > 0:
        issues.append(
            InsightIssue(
                id="iss-missing",
                severity="warning" if missing_labels > 20 else "info",
                category="missing",
                title=f"{missing_labels} rows have missing labels",
                description="Unlabeled rows will be skipped during training.",
                affected_rows=missing_labels,
                suggestion="Label or remove rows with missing labels before training.",
            )
        )

    if outliers > 0:
        issues.append(
            InsightIssue(
                id="iss-outliers",
                severity="info",
                category="outliers",
                title=f"{outliers} unusually long samples",
                description=(
                    f"Samples exceeding {round(outlier_threshold)} chars (2σ "
                    "above mean) may be truncated by the tokenizer."
                ),
                affected_rows=outliers,
                suggestion="Review and chunk or summarize long samples before training.",
            )
        )

    if std_len > mean_len * 1.5 and num_lengths > 10:
        issues.append(
            InsightIssue(
                id="iss-length",
                severity="info",
                category="length",
                title="High text length variance",
                description=(
                    f"Std dev: {round(std_len)} chars (mean: {round(mean_len)}). "
                    "Affects batch efficiency."
                ),
                affected_rows=0,
                suggestion="Use length bucketing in the data loader to improve GPU utilization.",
            )
        )

    return issues


def _compute_score(
    *,
    label_distribution: list[InsightLabelCount],
    scanned_rows: int,
    duplicate_rows: int,
    missing_labels: int,
    outliers: int,
    judge: JudgeStats | None,
) -> int:
    """Same formula as `qualityCalculator.ts`'s overall score, plus a judge
    blend when SDG-time judge aggregates are available: `0.75*base +
    0.25*judge.weighted.mean*100`, re-clamped to the same [15, 100] band.
    """
    min_pct = label_distribution[-1].percent if label_distribution else 100.0
    if min_pct < 5:
        score = 100 - 25
    elif min_pct < 10:
        score = 100 - 12
    else:
        score = 100

    denom = max(scanned_rows, 1)
    score -= min(15, round(duplicate_rows / denom * 100))
    score -= min(10, round(missing_labels / denom * 100))
    score -= min(8, outliers)
    score = max(15, min(100, score))

    if judge is not None:
        score = round(0.75 * score + 0.25 * judge.weighted.mean * 100)
        score = max(15, min(100, score))

    return int(score)


async def get_dataset_insights(
    db: AsyncSession, dataset_id: UUID, user: CurrentUser | None = None
) -> DatasetInsightsResponse:
    ds = await ownership.assert_dataset_access(db, dataset_id, user)
    if not ds.storage_uri:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dataset {dataset_id} has no rows yet (still generating?)",
        )

    rows, scan_truncated = _scan_rows(ds.storage_uri)
    scanned_rows = len(rows)
    row_count = ds.num_samples if ds.num_samples else scanned_rows

    text_field = _TEXT_FIELD[ds.task_type]
    label_applicable = ds.task_type != TaskType.QA

    # ---- labels ----------------------------------------------------------
    label_counts: dict[str, int] = {}
    missing_labels = 0
    if label_applicable:
        for row in rows:
            label = _extract_label(row, ds.task_type)
            if label is None:
                missing_labels += 1
            else:
                label_counts[label] = label_counts.get(label, 0) + 1
    total_labeled = sum(label_counts.values())
    label_distribution = sorted(
        (
            InsightLabelCount(
                label=label,
                count=count,
                percent=(
                    round(count / total_labeled * 100, 1) if total_labeled else 0.0
                ),
            )
            for label, count in label_counts.items()
        ),
        key=lambda item: item.count,
        reverse=True,
    )

    # ---- exact duplicates (whitespace-normalised, lowercased) -------------
    norm_counts: dict[str, int] = {}
    for row in rows:
        raw = row.get(text_field)
        norm = _normalise(raw) if isinstance(raw, str) else ""
        if not norm:
            continue
        norm_counts[norm] = norm_counts.get(norm, 0) + 1
    duplicate_rows = sum(c - 1 for c in norm_counts.values() if c > 1)

    # ---- length distribution / outliers -----------------------------------
    lengths: list[int] = [
        len(row[text_field])
        for row in rows
        if isinstance(row.get(text_field), str) and row[text_field]
    ]
    mean_len = statistics.fmean(lengths) if lengths else 0.0
    std_len = statistics.pstdev(lengths) if lengths else 0.0
    outlier_threshold = mean_len + 2 * std_len
    outliers = sum(1 for length in lengths if length > outlier_threshold and length > 600)

    length_distribution = [
        InsightLengthBucket(
            bucket=name,
            count=sum(1 for length in lengths if length >= lo and (hi is None or length <= hi)),
        )
        for name, lo, hi in _LENGTH_BUCKETS
    ]

    # ---- near-duplicates (MinHash LSH, same as the SDG generation loop) ---
    near_duplicate_count = MinHashDeduplicator().filter(rows, key=text_field).duplicates_dropped

    # ---- issues -------------------------------------------------------
    issues = _build_issues(
        label_distribution=label_distribution,
        total_labeled=total_labeled,
        duplicate_rows=duplicate_rows,
        missing_labels=missing_labels,
        outliers=outliers,
        outlier_threshold=outlier_threshold,
        mean_len=mean_len,
        std_len=std_len,
        num_lengths=len(lengths),
    )

    # ---- stored SDG aggregates (judge / generation-funnel counts) ---------
    judge, judge_by_key, counts = _map_stored_insights(ds.generation_metadata)

    # ---- score / readiness -------------------------------------------
    score = _compute_score(
        label_distribution=label_distribution,
        scanned_rows=scanned_rows,
        duplicate_rows=duplicate_rows,
        missing_labels=missing_labels,
        outliers=outliers,
        judge=judge,
    )
    readiness: str
    if score >= 85:
        readiness = "ready"
    elif score >= 65:
        readiness = "caveats"
    else:
        readiness = "fix"

    return DatasetInsightsResponse(
        dataset_id=ds.id,
        task_type=ds.task_type,
        row_count=row_count,
        scanned_rows=scanned_rows,
        scan_truncated=scan_truncated,
        label_distribution=label_distribution,
        near_duplicate_count=near_duplicate_count,
        duplicate_rows=duplicate_rows,
        missing_labels=missing_labels,
        outliers=outliers,
        length_distribution=length_distribution,
        issues=issues,
        overall_quality_score=score,
        readiness=readiness,  # type: ignore[arg-type]
        judge=judge,
        judge_by_key=judge_by_key,
        counts=counts,
    )


__all__ = ["get_dataset_insights", "INSIGHTS_SCAN_LIMIT"]
