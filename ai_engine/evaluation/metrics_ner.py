"""Exact entity type/span micro metrics for prepared NER JSON-array answers.

Offsets are Unicode code points, with an exclusive end. Invalid JSON or
entity schemas count as empty predictions and toward invalid_json_rate.
"""

import json


def _entities(answer: str) -> set[tuple[str, int, int]]:
    entities = json.loads(answer)
    if not isinstance(entities, list):
        raise ValueError("NER answer must be a JSON array")
    spans = set()
    for entity in entities:
        if not isinstance(entity, dict):
            raise ValueError("NER entity must be an object")
        label, start, end, text = (entity.get(k) for k in ("type", "start", "end", "text"))
        if (
            not isinstance(label, str)
            or not label
            or type(start) is not int
            or type(end) is not int
            or start < 0
            or end <= start
            or not isinstance(text, str)
            or len(text) != end - start
        ):
            raise ValueError("Invalid NER entity type/text/span")
        spans.add((label, start, end))
    return spans


def compute_metrics(*, predicted: list[str], expected: list[str]) -> dict:
    if not predicted or len(predicted) != len(expected):
        raise ValueError("NER metrics require nonempty parallel predictions and references")
    true_positive = predicted_count = expected_count = invalid = 0
    for prediction, reference in zip(predicted, expected, strict=True):
        gold = _entities(reference)  # Broken reference data is an error, not a model miss.
        try:
            actual = _entities(prediction)
        except (ValueError, TypeError):
            actual = set()
            invalid += 1
        true_positive += len(actual & gold)
        predicted_count += len(actual)
        expected_count += len(gold)
    precision = true_positive / predicted_count if predicted_count else 0.0
    recall = true_positive / expected_count if expected_count else 0.0
    return {
        "precision_micro": precision,
        "recall_micro": recall,
        "f1_micro": 2 * true_positive / (predicted_count + expected_count)
        if predicted_count + expected_count
        else 0.0,
        "invalid_json_rate": invalid / len(predicted),
        "n": len(predicted),
    }
