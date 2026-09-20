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
NER_LABELS = {
    "PERSON": "PERSON",
    "ORGANIZATION": "ORG",
    "LOCATION": "LOC",
    "DATE": "DATE",
    "MONEY": "MONEY",
}
SOURCE_ENTITY_TYPES = set(NER_LABELS) | {
    "TIME",
    "FACILITY",
    "URL",
    "PERCENT",
    "LEN",
    "AGO",
    "LAW",
    "PHONE",
    "EMAIL",
    "ZIP",
    "TEMPERATURE",
}
NER_INSTRUCTION = (
    "ดึงชื่อเอนทิตี PERSON, ORG, LOC, DATE, MONEY จากข้อความต่อไปนี้ "
    "ตอบเฉพาะ JSON array ของ object ที่มี text, type, start, end "
    "โดย start และ end เป็นตำแหน่งอักขระแบบเริ่มนับจาก 0 "
    "และ end ไม่รวมอักขระตำแหน่งนั้น ถ้าไม่พบให้ตอบ []\n\nข้อความ:\n"
)
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
    "tpl-004": {
        "directory": "thainer",
        "subdirectory": "data",
        "repo": "pythainlp/thainer-corpus-v2.2",
        "revision": "e516176cf83d96526e407a7652205d6620837600",
        "license": "CC-BY-3.0",
        "license_url": "https://creativecommons.org/licenses/by/3.0/",
        "attribution": "Wannaphong Phatthiyaphaibun (2024), Thai NER 2.2, DOI:10.5281/zenodo.10795907",
        "task": TaskType.QA,
    },
}


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def text_hash(text: str) -> str:
    """Whitespace/case/Unicode normalization only for leakage detection, not text edits."""
    normalized = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def source_text(row: dict, template_id: str) -> str:
    if template_id == "tpl-006":
        return row["texts"]
    return "".join(row["words"])


def preferred_partitions(partitions: dict[str, list[dict]], template_id: str) -> dict[str, str]:
    """Reserve every raw held-out input, even if later conversion rejects that row."""
    owners = {}
    for split in reversed(SPLITS):
        for row in partitions[split]:
            owners.setdefault(text_hash(source_text(row, template_id)), split)
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


def convert_ner(row: dict, names: list[str]) -> dict:
    words, ids = row["words"], row["ner"]
    if not words or len(words) != len(ids):
        raise ValueError("unaligned_ner_tokens")
    if any(not isinstance(word, str) or not word for word in words):
        raise ValueError("empty_or_invalid_ner_token")
    text = "".join(words)
    entities, active, start, offset = [], None, 0, 0

    def finish(end: int) -> None:
        if active in NER_LABELS:
            entities.append(
                {"text": text[start:end], "type": NER_LABELS[active], "start": start, "end": end}
            )

    for word, index in zip(words, ids, strict=True):
        tag = class_name(index, names)
        if tag != "O" and (
            len(tag) < 3 or tag[:2] not in {"B-", "I-"} or tag[2:] not in SOURCE_ENTITY_TYPES
        ):
            raise ValueError("invalid_bio_tag")
        if tag == "O":
            finish(offset)
            active = None
        elif tag.startswith("B-") and len(tag) > 2:
            finish(offset)
            active, start = tag[2:], offset
        elif tag.startswith("I-") and len(tag) > 2:
            if active != tag[2:]:
                raise ValueError("invalid_bio_continuation")
        else:
            raise ValueError("invalid_bio_tag")
        offset += len(word)
    finish(offset)
    return {
        "question": NER_INSTRUCTION + text,
        "answer": json.dumps(entities, ensure_ascii=False, separators=(",", ":")),
    }


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
    converter = convert_sentiment if template_id == "tpl-006" else convert_ner
    formatter, model = get_formatter(task), sample_model_for(task)
    candidates, seen, drops = [], set(), Counter()
    for index, original in enumerate(rows):
        text = source_text(original, template_id)
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
            row = converter(original, names)
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


def select_rows(candidates: list[dict], template_id: str, split: str) -> list[dict]:
    ordered = sorted(
        candidates,
        key=lambda item: hashlib.sha256(
            ("template-thai-v1:" + item["source_id"]).encode()
        ).hexdigest(),
    )
    if template_id == "tpl-004":
        return ordered if split == "train" else ordered[:500]
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
        label_feature = (
            feature["category"] if template_id == "tpl-006" else feature["ner"]["feature"]
        )
        names_by_split[split] = label_feature["names"]
        partitions[split] = table.to_pylist()
        raw_files.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": sha256_file(path),
                "url": f"https://huggingface.co/datasets/{source['repo']}/resolve/{source['revision']}/{relative}",
            }
        )
    owners = preferred_partitions(partitions, template_id)
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
    if template_id == "tpl-006":
        manifest["transformation"] = (
            "Drop question class, map pos/neu/neg to positive/neutral/negative, equal class sampling, no text rewriting."
        )
        manifest["limitations"] = [
            "Informal social posts are not a representative product-review population.",
            "Source anonymization can leave residual personal data.",
            "Prepared and token-validated; model quality has not been evaluated.",
        ]
    else:
        manifest["transformation"] = (
            "Join original tokens without inserted spaces; strictly decode BIO; map ORGANIZATION to ORG and LOCATION to LOC; keep PERSON/DATE/MONEY; ignore other entity types; one QA row per source sequence, answer JSON spans with Unicode code-point offsets, end exclusive."
        )
        manifest["limitations"] = [
            "NER source is news/PR/general text, not a dedicated customer-support corpus.",
            "Author states some original source lists were lost.",
            "README prose counts differ from parquet counts; manifest uses actual parquet rows.",
            "Exact normalized input deduplication does not detect every paraphrase or related document.",
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
        selected = select_rows(candidates, template_id, split)
        rows = [item["row"] for item in selected]
        sidecars = [
            {key: value for key, value in item.items() if key != "row"} for item in selected
        ]
        rows_path, ids_path = output / f"{split}.jsonl", output / f"{split}.provenance.jsonl"
        write_jsonl(rows_path, rows)
        write_jsonl(ids_path, sidecars)
        requested = (
            {"train": 6000, "validation": 600, "test": 900}[split]
            if template_id == "tpl-006"
            else (None if split == "train" else 500)
        )
        counts = (
            Counter(row["label"] for row in rows)
            if template_id == "tpl-006"
            else Counter(entity["type"] for row in rows for entity in json.loads(row["answer"]))
        )
        manifest["splits"][split] = {
            "source_rows": len(partitions[split]),
            "eligible_rows": len(candidates),
            "requested_rows": requested,
            "rows": len(rows),
            "shortfall": max(0, requested - len(rows)) if requested is not None else 0,
            "dropped": dict(sorted(drops.items())),
            "not_selected": len(candidates) - len(rows),
            "label_counts" if template_id == "tpl-006" else "entity_counts": dict(
                sorted(counts.items())
            ),
            "max_tokens": max((item["tokens"] for item in selected), default=0),
            "path": rows_path.name,
            "sha256": sha256_file(rows_path),
            "provenance_path": ids_path.name,
            "provenance_sha256": sha256_file(ids_path),
        }
        if template_id == "tpl-004":
            manifest["splits"][split]["empty_entity_rows"] = sum(
                row["answer"] == "[]" for row in rows
            )
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
