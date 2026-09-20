"""Prepare pinned Thai template datasets offline; never starts training or services.

Run from the repository root with ``python -m scripts.prepare_template_thai``.
Raw parquet files and the pinned Qwen tokenizer must already exist under --root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from ai_engine.training.data_formatters import get_formatter
from api.schemas.data_formats import sample_model_for
from api.schemas.enums import TaskType

SPLITS = ("train", "validation", "test")
TOKEN_LIMIT = 2048
TOKENIZER_REPO = "Qwen/Qwen2.5-1.5B-Instruct"
TOKENIZER_REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
SENTIMENT_LABELS = {"pos": "positive", "neu": "neutral", "neg": "negative"}
SOURCES = {
    "tpl-006": {
        "directory": "wisesight",
        "subdirectory": "wisesight_sentiment",
        "repo": "pythainlp/wisesight_sentiment",
        "revision": "85a79ed833429457227182c9f61a83e9127dfa2e",
        "license": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "attribution": "Wisesight (Thailand) Co., Ltd. and PyThaiNLP contributors",
        "task": TaskType.CLASSIFICATION,
    },
}


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def text_hash(text: str) -> str:
    """Whitespace/case/Unicode normalization only for leakage detection, not text edits."""
    normalized = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def source_text(row: dict) -> str:
    return row["texts"]


def preferred_partitions(partitions: dict[str, list[dict]]) -> dict[str, str]:
    """Reserve every raw held-out input, even if later conversion rejects that row."""
    owners = {}
    for split in reversed(SPLITS):
        for row in partitions[split]:
            owners.setdefault(text_hash(source_text(row)), split)
    return owners


def class_name(index: int, names: list[str]) -> str:
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(names):
        raise ValueError("invalid_label_id")
    return names[index]


def convert_sentiment(row: dict, names: list[str]) -> dict:
    label = class_name(row["category"], names)
    if label == "q":
        raise ValueError("question_class")
    if label not in SENTIMENT_LABELS:
        raise ValueError("unknown_sentiment_label")
    return {"text": row["texts"], "label": SENTIMENT_LABELS[label]}


def prepare_partition(
    rows: list[dict],
    *,
    split: str,
    template_id: str,
    names: list[str],
    owners: dict[str, str],
    tokenizer: Any,
) -> tuple[list[dict], Counter]:
    task = SOURCES[template_id]["task"]
    formatter, model = get_formatter(task), sample_model_for(task)
    candidates, seen, drops = [], set(), Counter()
    for index, original in enumerate(rows):
        text = source_text(original)
        fingerprint = text_hash(text)
        if not text.strip():
            drops["empty_input"] += 1
            continue
        if owners[fingerprint] != split:
            drops["reserved_for_other_raw_split"] += 1
            continue
        if fingerprint in seen:
            drops["duplicate_input_within_split"] += 1
            continue
        seen.add(fingerprint)
        try:
            row = convert_sentiment(original, names)
        except ValueError as exc:
            drops[str(exc)] += 1
            continue
        model.model_validate(row)
        tokens = len(
            tokenizer.apply_chat_template(
                formatter(row),
                tokenize=True,
                add_generation_prompt=False,
            )
        )
        if tokens > TOKEN_LIMIT:
            drops["over_2048_tokens"] += 1
            continue
        candidates.append(
            {
                "row": row,
                "source_id": f"{split}:{index}",
                "input_sha256": fingerprint,
                "tokens": tokens,
            }
        )
    return candidates, drops


def select_rows(candidates: list[dict], split: str) -> list[dict]:
    ordered = sorted(
        candidates,
        key=lambda item: hashlib.sha256(
            ("template-thai-v1:" + item["source_id"]).encode()
        ).hexdigest(),
    )
    per_class = {"train": 2000, "validation": 200, "test": 300}[split]
    groups = {
        label: [item for item in ordered if item["row"]["label"] == label]
        for label in SENTIMENT_LABELS.values()
    }
    # Keep balance if cleaning leaves too few examples; manifest reports the shortfall.
    count = min(per_class, *(len(group) for group in groups.values()))
    selected = {item["source_id"] for group in groups.values() for item in group[:count]}
    return [item for item in ordered if item["source_id"] in selected]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def prepare(root: Path, template_id: str, tokenizer: Any, tokenizer_hashes: dict) -> dict:
    import pyarrow.parquet as pq

    source = SOURCES[template_id]
    source_root = root / "sources" / source["directory"]
    partitions, names_by_split, raw_files = {}, {}, []
    for split in SPLITS:
        relative = f"{source['subdirectory']}/{split}-00000-of-00001.parquet"
        path = source_root / relative
        table = pq.read_table(path)
        feature = json.loads(table.schema.metadata[b"huggingface"])["info"]["features"]
        label_feature = feature["category"]
        names_by_split[split] = label_feature["names"]
        partitions[split] = table.to_pylist()
        raw_files.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": sha256_file(path),
                "url": f"https://huggingface.co/datasets/{source['repo']}/resolve/{source['revision']}/{relative}",
            }
        )
    owners = preferred_partitions(partitions)
    output = root / "prepared" / template_id
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "template_id": template_id,
        "task_type": source["task"].value,
        "source": {
            key: source[key]
            for key in ("repo", "revision", "license", "license_url", "attribution")
        },
        "raw_files": raw_files,
        "tokenizer": {
            "repo": TOKENIZER_REPO,
            "revision": TOKENIZER_REVISION,
            "files_sha256": tokenizer_hashes,
            "max_tokens": TOKEN_LIMIT,
            "method": "existing task formatter + apply_chat_template(tokenize=True, add_generation_prompt=False), no truncation",
        },
        "split_policy": "Preserve original splits; reserve normalized inputs across ALL raw rows with test > validation > train precedence; retain first occurrence per split; deterministic SHA-256 ordering.",
        "normalization": "NFKC, casefold, collapse whitespace for fingerprint only; original text retained.",
        "splits": {},
    }
    manifest["transformation"] = (
        "Drop question class, map pos/neu/neg to positive/neutral/negative, equal class sampling, no text rewriting."
    )
    manifest["limitations"] = [
        "Informal social posts are not a representative product-review population.",
        "Source anonymization can leave residual personal data.",
        "Prepared and token-validated; model quality has not been evaluated.",
    ]
    readme = source_root / "README.md"
    license_note = output / "SOURCE_LICENSE.md"
    license_note.write_text(
        f"# Source and license\n\n{source['attribution']}\n\n"
        f"License: [{source['license']}]({source['license_url']}).\n\n"
        f"Source: https://huggingface.co/datasets/{source['repo']}/tree/{source['revision']}\n\n"
        f"Changes: {manifest['transformation']}\n\n## Original dataset card\n\n"
        + readme.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest["source_card"] = {"path": str(readme.relative_to(root)), "sha256": sha256_file(readme)}
    manifest["license_file"] = {"path": license_note.name, "sha256": sha256_file(license_note)}
    for split in SPLITS:
        candidates, drops = prepare_partition(
            partitions[split],
            split=split,
            template_id=template_id,
            names=names_by_split[split],
            owners=owners,
            tokenizer=tokenizer,
        )
        selected = select_rows(candidates, split)
        rows = [item["row"] for item in selected]
        sidecars = [
            {key: value for key, value in item.items() if key != "row"} for item in selected
        ]
        rows_path, ids_path = output / f"{split}.jsonl", output / f"{split}.provenance.jsonl"
        write_jsonl(rows_path, rows)
        write_jsonl(ids_path, sidecars)
        requested = {"train": 6000, "validation": 600, "test": 900}[split]
        counts = Counter(row["label"] for row in rows)
        manifest["splits"][split] = {
            "source_rows": len(partitions[split]),
            "eligible_rows": len(candidates),
            "requested_rows": requested,
            "rows": len(rows),
            "shortfall": max(0, requested - len(rows)) if requested is not None else 0,
            "dropped": dict(sorted(drops.items())),
            "not_selected": len(candidates) - len(rows),
            "label_counts": dict(sorted(counts.items())),
            "max_tokens": max((item["tokens"] for item in selected), default=0),
            "path": rows_path.name,
            "sha256": sha256_file(rows_path),
            "provenance_path": ids_path.name,
            "provenance_sha256": sha256_file(ids_path),
        }
    manifest["status"] = (
        "prepared"
        if not any(split["shortfall"] for split in manifest["splits"].values())
        else "partial"
    )
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/template-catalog"))
    args = parser.parse_args()
    from transformers import AutoTokenizer

    tokenizer_path = args.root / "tokenizer"
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_path, local_files_only=True, trust_remote_code=False
    )
    tokenizer_hashes = {
        path.name: sha256_file(path) for path in sorted(tokenizer_path.iterdir()) if path.is_file()
    }
    for template_id in SOURCES:
        manifest = prepare(args.root, template_id, tokenizer, tokenizer_hashes)
        print(
            json.dumps(
                {
                    "template_id": template_id,
                    "status": manifest["status"],
                    "splits": {
                        name: {key: split[key] for key in ("rows", "max_tokens", "sha256")}
                        for name, split in manifest["splits"].items()
                    },
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
