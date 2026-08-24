"""Prompt builders for the Phase 9 SDG pipeline.

Five prompt families live here:

  • Generator — RTC-FO templates per task type. Each call asks for
    `CANDIDATES_PER_GEN_CALL` candidates so we get throughput per request.

  • Judge — task-aware rubric (fidelity / naturalness / utility),
    JSON-mode output.

  • Meta-prompter — diversity-rule + sentinel-rule generator. Output
    parsed by `meta_prompter.parse_meta_response`.

  • Format Detection — schema mismatch + key-rename. Lives in
    `format_detector.py` (built inline because it needs the canonical
    key set), so this file does not export a Format Detection builder.

  • PDF → QA — multimodal Generator for the first iteration of QA + PDF.

The old Phase 4 builders (`build_prompt`, `Prompt`, `JSON_OBJECT_RESPONSE_FORMAT`)
are kept here in spirit: `Prompt` remains the dataclass, and the
`JSON_OBJECT_RESPONSE_FORMAT` constant is re-exported. The dispatcher
function name changes — Generator is now `build_generator_prompt` since
the same module also builds Judge prompts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from api.schemas.data_formats import ToolDefinition
from api.schemas.enums import TaskType

from .constants import CANDIDATES_PER_GEN_CALL


@dataclass(frozen=True)
class Prompt:
    """A single (system, user) prompt pair."""

    system: str
    user: str


JSON_OBJECT_RESPONSE_FORMAT = {"type": "json_object"}
"""OpenRouter response_format for JSON-mode."""


# ---------------------------------------------------------------------------
# Generator system prompts
# ---------------------------------------------------------------------------


_GENERATOR_BASE_SYSTEM = (
    "You are a synthetic data generation assistant for fine-tuning small "
    "language models. You produce high-quality, diverse training data in "
    "strict JSON format. You never include explanations or markdown — only "
    "the JSON object matching the schema requested."
)

_GENERATOR_SYSTEMS: dict[TaskType, str] = {
    TaskType.CLASSIFICATION: (
        f"{_GENERATOR_BASE_SYSTEM}\n\n"
        "[Role] You generate CLASSIFICATION training data. Each output row "
        'is {"text": "<input>", "label": "<one of the closed label set>"}.'
    ),
    TaskType.TOOL_CALLING: (
        f"{_GENERATOR_BASE_SYSTEM}\n\n"
        "[Role] You generate TOOL CALLING training data. Each output row "
        'is {"question": "<user instruction>", "answer": "<JSON STRING '
        'containing \\"name\\" and \\"parameters\\">"}. The `answer` field '
        "MUST be a string (JSON-encoded), not an object."
    ),
    TaskType.QA: (
        f"{_GENERATOR_BASE_SYSTEM}\n\n"
        "[Role] You generate QUESTION-ANSWERING training data. Each output "
        'row is {"question": "<user question>", "answer": "<helpful answer>"}.'
    ),
}


# ---------------------------------------------------------------------------
# Generator user prompts (RTC-FO)
# ---------------------------------------------------------------------------


def build_generator_prompt(
    task_type: TaskType,
    *,
    task_description: str,
    label_or_tool: str | None,
    examples: list[dict[str, Any]] | None,
    diversity_rule: str,
    difficulty: str,
    classification_labels: list[str] | None = None,
    tool_definitions: list[ToolDefinition] | None = None,
    is_sentinel: bool = False,
) -> Prompt:
    """Build one Generator prompt requesting CANDIDATES_PER_GEN_CALL outputs.

    Args:
        task_type: which sample shape the model emits.
        task_description: user-supplied natural-language task spec.
        label_or_tool: target label (classification) or tool name
            (tool_calling). None for QA.
        examples: a few-shot pool drawn from seeds (or empty for sentinel rows).
        diversity_rule: one rule from the rotating coverage pool.
        difficulty: one of `DIFFICULTY_LEVELS` from the rotating pool.
        classification_labels: full closed label set (incl. sentinel) for
            classification — needed inside the prompt so the model can
            confirm its picks.
        tool_definitions: full tool catalog (incl. sentinel) for
            tool_calling.
        is_sentinel: True when the row is intended to land in the sentinel
            class (`unknown` / `no_tool_needed`).
    """
    system = _GENERATOR_SYSTEMS[task_type]
    user = _build_generator_user(
        task_type=task_type,
        task_description=task_description,
        label_or_tool=label_or_tool,
        examples=examples,
        diversity_rule=diversity_rule,
        difficulty=difficulty,
        classification_labels=classification_labels,
        tool_definitions=tool_definitions,
        is_sentinel=is_sentinel,
    )
    return Prompt(system=system, user=user)


def _build_generator_user(
    *,
    task_type: TaskType,
    task_description: str,
    label_or_tool: str | None,
    examples: list[dict[str, Any]] | None,
    diversity_rule: str,
    difficulty: str,
    classification_labels: list[str] | None,
    tool_definitions: list[ToolDefinition] | None,
    is_sentinel: bool,
) -> str:
    parts: list[str] = []
    parts.append(f"[Task]\n{task_description.strip()}")

    if task_type is TaskType.CLASSIFICATION:
        if classification_labels is not None:
            parts.append(
                "[Closed label set — never invent a new label]\n"
                + json.dumps(classification_labels, ensure_ascii=False)
            )
        if is_sentinel:
            parts.append(
                f"[Target label]\n{label_or_tool!r} — generate inputs that "
                "DO NOT fit any of the real labels above. Off-topic, "
                "ambiguous, or out-of-domain prompts."
            )
        else:
            parts.append(f"[Target label]\n{label_or_tool!r}")
    elif task_type is TaskType.TOOL_CALLING:
        if tool_definitions is not None:
            tools_payload = [t.model_dump(mode="json") for t in tool_definitions]
            parts.append(
                "[Available tools — only invoke from this list; parameter "
                "names + types must match]\n"
                + json.dumps(tools_payload, ensure_ascii=False, indent=2)
            )
        if is_sentinel:
            parts.append(
                f"[Target tool]\n{label_or_tool!r} — generate user inputs "
                "that do NOT match any real tool (off-topic small talk, "
                "ambiguous queries, requests outside the catalog). The "
                "answer should call the sentinel tool with no parameters."
            )
        else:
            parts.append(f"[Target tool]\n{label_or_tool!r}")
    # QA has no per-row label / tool — nothing to add here.

    if examples:
        parts.append(
            f"[Few-shot examples ({len(examples)} rows)]\n"
            + json.dumps(examples, ensure_ascii=False, indent=2)
        )

    parts.append(
        f"[Diversity rule for this batch]\n{diversity_rule}\n\n"
        f"[Difficulty]\n{difficulty}"
    )
    parts.append(_generator_output_instructions(task_type))
    return "\n\n".join(parts)


def _generator_output_instructions(task_type: TaskType) -> str:
    if task_type is TaskType.CLASSIFICATION:
        row_shape = '{"text": "...", "label": "..."}'
    elif task_type is TaskType.TOOL_CALLING:
        row_shape = (
            '{"question": "...", "answer": "{\\"name\\":\\"...\\","'
            '"parameters\\":{...}}"}'
        )
    else:
        row_shape = '{"question": "...", "answer": "..."}'
    return (
        "[Output Instructions]\n"
        f"1. Produce EXACTLY {CANDIDATES_PER_GEN_CALL} rows in the schema {row_shape}.\n"
        "2. Apply the diversity rule and difficulty above to keep this "
        "batch distinct from anything that came before.\n"
        "3. Output ONLY this JSON object (no prose, no markdown fences):\n"
        f'   {{"samples": [<{CANDIDATES_PER_GEN_CALL} rows>]}}'
    )


# ---------------------------------------------------------------------------
# Judge prompts
# ---------------------------------------------------------------------------


_JUDGE_SYSTEM = (
    "You are an evaluator of synthetic training data. You score one row "
    "on three axes — fidelity, naturalness, utility — each in [0.0, 1.0]. "
    "You output ONLY a JSON object matching the schema; no prose, no "
    "markdown, no explanation outside the `reasoning` field."
)


def build_judge_prompt(
    task_type: TaskType,
    *,
    task_description: str,
    rows: list[dict[str, Any]],
    classification_labels: list[str] | None = None,
    tool_definitions: list[ToolDefinition] | None = None,
) -> Prompt:
    """Build one Judge prompt for a batch of candidate rows.

    Detects sentinel rows (label="unknown" for classification, answer.name=
    "no_tool_needed" for tool_calling) and layers in a sentinel-specific
    rubric so the Judge doesn't reject them for "not fitting a real label" —
    the whole point of a sentinel is to capture off-topic / out-of-scope
    content. Chunking (how many rows go in one call) is the generator's
    job — this builder just renders whatever list it is given.
    """
    if not rows:
        raise ValueError("build_judge_prompt requires at least one row")

    sentinel_flags = [_row_is_sentinel(task_type, row) for row in rows]
    indexed_rows = [
        {"index": i, "sentinel": sentinel_flags[i], "row": row}
        for i, row in enumerate(rows)
    ]
    any_sentinel = any(sentinel_flags)

    rubric = _judge_rubric(task_type, is_sentinel=False)
    parts = [
        f"[Task description]\n{task_description.strip()}",
        f"[Rows to evaluate]\n{json.dumps(indexed_rows, ensure_ascii=False, indent=2)}",
        f"[Rubric]\n{rubric}",
    ]
    if any_sentinel:
        parts.append(
            "[Sentinel rows]\n"
            'Rows with "sentinel": true were deliberately produced for the '
            "off-topic / out-of-scope sentinel class. Score them HIGH if "
            "they genuinely fail to fit any real class/tool (which is what "
            "makes them useful training data for teaching the model when to "
            "refuse). Do NOT penalise those rows for not matching a real "
            "label or tool — that mismatch IS the point."
        )
        sentinel_rubric = _judge_rubric(task_type, is_sentinel=True)
        parts.append(
            "[Sentinel Rubric — apply to rows with \"sentinel\": true instead "
            f"of the rubric above]\n{sentinel_rubric}"
        )
    if task_type is TaskType.CLASSIFICATION and classification_labels is not None:
        parts.append(
            "[Closed label set]\n"
            + json.dumps(classification_labels, ensure_ascii=False)
        )
    if task_type is TaskType.TOOL_CALLING and tool_definitions is not None:
        parts.append(
            "[Tool catalog]\n"
            + json.dumps(
                [t.model_dump(mode="json") for t in tool_definitions],
                ensure_ascii=False,
                indent=2,
            )
        )
    parts.append(
        "[Output Instructions]\n"
        "Output ONLY this JSON object (no markdown, no prose outside "
        "`reasoning`).\n"
        "One entry per input row, same `index` values, in the same order:\n"
        '{"scores": [{"index": 0, "reasoning": "<=120 chars", "fidelity": '
        '<0..1>, "naturalness": <0..1>, "utility": <0..1>}]}'
    )
    return Prompt(system=_JUDGE_SYSTEM, user="\n\n".join(parts))


def _row_is_sentinel(task_type: TaskType, row: dict[str, Any]) -> bool:
    """Detect whether the row was generated for the sentinel class."""
    # Local imports — `prompts` should not eagerly depend on constants
    # (which transitively pull MinIO etc).
    from .constants import (
        CLASSIFICATION_SENTINEL_LABEL,
        TOOL_CALLING_SENTINEL_NAME,
    )

    if task_type is TaskType.CLASSIFICATION:
        return str(row.get("label", "")).strip() == CLASSIFICATION_SENTINEL_LABEL
    if task_type is TaskType.TOOL_CALLING:
        try:
            inner = json.loads(row.get("answer", "{}"))
            return str(inner.get("name", "")).strip() == TOOL_CALLING_SENTINEL_NAME
        except (ValueError, TypeError):
            return False
    return False  # QA has no sentinel


def _judge_rubric(task_type: TaskType, *, is_sentinel: bool = False) -> str:
    if task_type is TaskType.QA:
        return (
            "fidelity     — does the answer correctly answer the question, "
            "consistent with the task description?\n"
            "naturalness  — would a real user phrase the question this way "
            "and find this answer helpful?\n"
            "utility      — is this Q&A pair useful training material for "
            "the described task?"
        )
    if task_type is TaskType.CLASSIFICATION:
        if is_sentinel:
            return (
                "fidelity     — is the text genuinely off-topic / out-of-scope "
                "for the closed real-label set, making 'unknown' the correct "
                "catch-all assignment? Score HIGH (>=0.8) if YES.\n"
                "naturalness  — would a real user actually send this kind of "
                "off-topic / ambiguous message in any context?\n"
                "utility      — is this 'unknown'-labeled row useful training "
                "material for teaching the classifier when to refuse "
                "classification?"
            )
        return (
            "fidelity     — does the text actually belong to the assigned "
            "label given the task description?\n"
            "naturalness  — would a real user write text like this?\n"
            "utility      — is this row useful training material for the "
            "described classifier?"
        )
    # tool_calling
    if is_sentinel:
        return (
            "fidelity     — is the user's question genuinely outside the "
            "available tool catalog (off-topic, ambiguous, or for a tool "
            "that simply does not exist in the catalog), making "
            "'no_tool_needed' the correct refusal? Score HIGH (>=0.8) if YES.\n"
            "naturalness  — would a real user phrase such an out-of-scope "
            "request this way?\n"
            "utility      — is this 'no_tool_needed' row useful training "
            "material for teaching the model when to refuse to call any tool?"
        )
    return (
        "fidelity     — given the user's question, is the chosen tool + "
        "parameter set the correct one?\n"
        "naturalness  — would a real user phrase the request this way?\n"
        "utility      — is this row useful training material for the "
        "described tool-calling task?"
    )


# ---------------------------------------------------------------------------
# Meta-prompter (diversity rules)
# ---------------------------------------------------------------------------


_META_SYSTEM = (
    "You design diversity rules for a synthetic data generation pipeline. "
    "You output ONLY a JSON object matching the schema; no prose, no "
    "markdown, no explanation."
)


def build_meta_prompt(
    task_type: TaskType,
    *,
    task_description: str,
    classification_labels: list[str] | None = None,
    tool_definitions: list[ToolDefinition] | None = None,
    include_unknown: bool = False,
) -> Prompt:
    """Build the one-shot meta-prompt asked at job start.

    Output is parsed by `meta_prompter.parse_meta_response(include_unknown=...)`.
    """
    parts = [
        f"[Task type]\n{task_type.value}",
        f"[Task description]\n{task_description.strip()}",
    ]
    if task_type is TaskType.CLASSIFICATION and classification_labels is not None:
        parts.append(
            "[Label set]\n" + json.dumps(classification_labels, ensure_ascii=False)
        )
    if task_type is TaskType.TOOL_CALLING and tool_definitions is not None:
        parts.append(
            "[Tool catalog]\n"
            + json.dumps(
                [t.model_dump(mode="json") for t in tool_definitions],
                ensure_ascii=False,
                indent=2,
            )
        )

    schema_part: str
    if include_unknown:
        schema_part = (
            '{"diversity_rules": ["...", ... (>=8)], '
            '"unknown_diversity_rules": ["...", ... (>=5)]}'
        )
        unknown_clause = (
            "Also produce >= 5 rules describing OFF-TOPIC / out-of-scope / "
            "ambiguous user inputs that should land in the sentinel class.\n"
        )
    else:
        schema_part = '{"diversity_rules": ["...", ... (>=8)]}'
        unknown_clause = ""

    parts.append(
        "[Output Instructions]\n"
        "Produce >= 8 stylistic diversity rules covering topic, phrasing, "
        "register, length, and edge cases for synthetic rows in this task. "
        "Each rule is one short sentence.\n"
        f"{unknown_clause}"
        f"Output ONLY this JSON object: {schema_part}"
    )

    return Prompt(system=_META_SYSTEM, user="\n\n".join(parts))


# ---------------------------------------------------------------------------
# PDF → QA (multimodal)
# ---------------------------------------------------------------------------


_PDF_QA_SYSTEM = (
    "You extract question-answer training pairs from documents. You output "
    "ONLY a JSON object matching the schema; no prose, no markdown."
)


def build_pdf_qa_messages(
    *,
    task_description: str,
    num_samples: int,
    pdf_data_url: str,
) -> list[dict[str, Any]]:
    """Build the multimodal messages list for one PDF → QA call.

    Returns the raw OpenAI-format messages (caller passes to
    `OpenRouterClient.chat_raw(messages=...)`).
    """
    text_part = (
        f"[Task description]\n{task_description.strip()}\n\n"
        f"[Output Instructions]\n"
        f"1. Read the attached PDF and produce {num_samples} diverse, "
        "factually-grounded Q&A pairs that test understanding of its content.\n"
        "2. Each Q&A pair must be answerable from the document alone.\n"
        "3. Vary question style: factual recall, comparison, 'why', procedural.\n"
        "4. Answers should be concise (1–4 sentences) grounded in document text.\n"
        '5. Output ONLY: {"samples": [{"question": "...", "answer": "..."}, ...]}'
    )
    return [
        {"role": "system", "content": _PDF_QA_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text_part},
                {
                    "type": "file",
                    "file": {
                        "filename": "seed.pdf",
                        "file_data": pdf_data_url,
                    },
                },
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Generator response parsing
# ---------------------------------------------------------------------------


def parse_generator_response(raw: str) -> list[dict[str, Any]]:
    """Parse `{"samples": [...]}`. Tolerates accidental markdown fences.

    Raises `ValueError` if the JSON shape is wrong (caller treats this as
    a parse failure for the loop's failure counter).
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.lstrip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.split("```", 1)[0].strip()
    obj = json.loads(text)
    if not isinstance(obj, dict) or "samples" not in obj:
        raise ValueError("response missing 'samples' key")
    samples = obj["samples"]
    if not isinstance(samples, list):
        raise ValueError("'samples' must be a JSON array")
    # Drop non-dict elements defensively.
    return [s for s in samples if isinstance(s, dict)]


__all__ = [
    "Prompt",
    "JSON_OBJECT_RESPONSE_FORMAT",
    "build_generator_prompt",
    "build_judge_prompt",
    "build_meta_prompt",
    "build_pdf_qa_messages",
    "parse_generator_response",
]
