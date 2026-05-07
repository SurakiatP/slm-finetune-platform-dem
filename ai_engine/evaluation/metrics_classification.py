"""Classification metrics — accuracy, macro F1, confusion matrix.

Pure domain code. `scikit-learn` is in `[eval]` extras (worker container only)
so we defer the import to function-call time.
"""

from __future__ import annotations

from typing import Any


def compute_metrics(
    *,
    predicted: list[str],
    expected: list[str],
    labels: list[str],
) -> dict[str, Any]:
    """Compute accuracy, macro F1, and a confusion matrix.

    Args:
        predicted: model outputs (one label per row).
        expected: gold labels (parallel to predicted).
        labels: closed set of valid labels (defines confusion-matrix axis order
            and ensures off-set predictions don't crash sklearn).

    Returns:
        ``{"accuracy", "f1_macro", "f1_per_label", "confusion_matrix",
            "out_of_set_predictions", "n"}``
    """
    if len(predicted) != len(expected):
        raise ValueError(
            f"predicted/expected length mismatch: {len(predicted)} vs {len(expected)}"
        )
    if not predicted:
        raise ValueError("compute_metrics: empty inputs")
    if not labels:
        raise ValueError("compute_metrics: labels list is empty")

    # Deferred import — only available in worker container.
    from sklearn.metrics import (  # type: ignore[import-not-found]
        accuracy_score,
        confusion_matrix,
        f1_score,
    )

    label_set = set(labels)
    out_of_set = sum(1 for p in predicted if p not in label_set)

    # Sklearn handles unknown labels gracefully when `labels=` is passed
    # explicitly: rows with predictions outside the set still count against
    # accuracy + per-label F1 (treated as wrong).
    accuracy = float(accuracy_score(expected, predicted))
    f1_macro = float(
        f1_score(expected, predicted, labels=labels, average="macro", zero_division=0)
    )
    f1_per = f1_score(expected, predicted, labels=labels, average=None, zero_division=0)
    f1_per_label = {label: float(score) for label, score in zip(labels, f1_per)}

    cm = confusion_matrix(expected, predicted, labels=labels).tolist()

    return {
        "accuracy": accuracy,
        "f1_macro": f1_macro,
        "f1_per_label": f1_per_label,
        "confusion_matrix": cm,
        "labels": list(labels),
        "out_of_set_predictions": int(out_of_set),
        "n": len(predicted),
    }


__all__ = ["compute_metrics"]
