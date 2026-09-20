"""Acquire text-only clearOCR and prepare a partial, unavailable invoice starter."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_engine.training.data_formatters import format_qa
from api.schemas.data_formats import QASample
from scripts.prepare_template_qa import TOKENIZER_REVISION, digest, normalized

REPO = "Lukaszl/clearocr-invoice-document-ai"
REVISION = "86d1b56914f75861397c97ebd70cc203b15c03f3"
SPLITS = ("train", "validation", "test")
ITEM_FIELDS = (
    "item_desc",
    "item_qty",
    "item_net_price",
    "item_net_worth",
    "item_vat",
    "item_gross_worth",
)


def safe_path(split, relative):
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.parts[0] not in ("ocr", "json")
    ):
        raise ValueError("Unexpected source artifact path")
    return f"{split}/{path}"


def download_sources(destination):
    from huggingface_hub import hf_hub_download

    def fetch(filename):
        hf_hub_download(
            REPO,
            filename,
            repo_type="dataset",
            revision=REVISION,
            token=False,
            local_dir=destination,
        )

    for filename in ("README.md", "docs/ATTRIBUTION.md", *(f"{s}/metadata.jsonl" for s in SPLITS)):
        fetch(filename)
    filenames = set()
    for split in SPLITS:
        for line in (destination / split / "metadata.jsonl").read_text().splitlines():
            row = json.loads(line)
            filenames.update(
                safe_path(split, row[key]) for key in ("clearocr_text_path", "invoice_json_path")
            )
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, sorted(filenames)))


def canonical_answer(payload):
    header, summary = payload["header"], payload["summary"]
    items = [{field: item[field] for field in ITEM_FIELDS} for item in payload["items"]]
    if not items:
        raise ValueError("missing_fields")
    answer = {
        "vendor": header["seller"],
        "date": header["invoice_date"],
        "invoice_number": header["invoice_no"],
        "items": items,
        "subtotal": summary["total_net_worth"],
        "tax": summary["total_vat"],
        "total": summary["total_gross_worth"],
    }
    values = [v for k, v in answer.items() if k != "items"] + [
        v for item in items for v in item.values()
    ]
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("missing_fields")
    return answer, values


def prepare_records(records, token_count):
    """Reserve full invoice/input components for held-out splits before filtering."""
    parents, keys = {}, []

    def find(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for record in records:
        group_keys = ["text:" + digest(normalized(record["text"]))]
        header = record["data"].get("header", {})
        if isinstance(header.get("invoice_no"), str) and isinstance(header.get("seller"), str):
            group_keys.append(
                "invoice:"
                + digest(
                    json.dumps([normalized(header["seller"]), normalized(header["invoice_no"])])
                )
            )
        for key in group_keys:
            parents[find(key)] = find(group_keys[0])
        keys.append(group_keys[0])
    groups = [find(key) for key in keys]
    priorities = {}
    for record, group in zip(records, groups, strict=True):
        priorities[group] = max(priorities.get(group, -1), SPLITS.index(record["split"]))
    result = {split: [] for split in SPLITS}
    dropped, seen = Counter(), set()
    for record, group in zip(records, groups, strict=True):
        split = record["split"]
        if SPLITS.index(split) != priorities[group]:
            dropped["heldout_invoice"] += 1
            continue
        if group in seen:
            dropped["duplicate_invoice"] += 1
            continue
        try:
            answer, values = canonical_answer(record["data"])
        except (KeyError, TypeError, ValueError):
            dropped["missing_fields"] += 1
            continue
        text = record["text"]
        if any(normalized(value) not in normalized(text) for value in values):
            dropped["ungrounded_value"] += 1
            continue
        sample = {
            "question": "Extract the invoice details from the following OCR text. Return only JSON "
            "with vendor, date, invoice_number, items, subtotal, tax and total. "
            "For each item use item_desc, item_qty, item_net_price, item_net_worth, "
            "item_vat and item_gross_worth. Preserve source values as strings.\n\nOCR text:\n"
            + text,
            "answer": json.dumps(answer, ensure_ascii=False, sort_keys=True),
        }
        QASample.model_validate(sample)
        tokens = token_count(format_qa(sample))
        if tokens > 2048:
            dropped["over_token_limit"] += 1
            continue
        seen.add(group)
        result[split].append(
            (
                sample,
                {
                    "source_id": record["source_id"],
                    "source_split": split,
                    "document_group": digest(group),
                    "input_sha256": digest(normalized(text)),
                    "tokens": tokens,
                    "source_files": record.get("source_files", []),
                },
            )
        )
    return result, dropped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/template-catalog"))
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    source = args.root / "sources/invoice"
    if args.download:
        download_sources(source)
    records, source_files = [], {}

    def read(path):
        payload = (source / path).read_bytes()
        source_files[path] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
        return payload.decode("utf-8")

    for name in ("README.md", "docs/ATTRIBUTION.md"):
        read(name)
    for split in SPLITS:
        for line in read(f"{split}/metadata.jsonl").splitlines():
            row = json.loads(line)
            paths = [
                safe_path(split, row[key]) for key in ("clearocr_text_path", "invoice_json_path")
            ]
            records.append(
                {
                    "split": split,
                    "source_id": row["source_id"],
                    "text": read(paths[0]),
                    "data": json.loads(read(paths[1])),
                    "source_files": paths,
                }
            )
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        args.root / "tokenizer", local_files_only=True, trust_remote_code=False
    )
    result, dropped = prepare_records(
        records,
        lambda m: len(tokenizer.apply_chat_template(m, tokenize=True, add_generation_prompt=False)),
    )
    destination = args.root / "prepared/tpl-002"
    destination.mkdir(parents=True, exist_ok=True)
    files = {}
    for split, items in result.items():
        for suffix, index in (("jsonl", 0), ("provenance.jsonl", 1)):
            payload = "".join(
                json.dumps(item[index], ensure_ascii=False, sort_keys=True) + "\n" for item in items
            )
            name = f"{split}.{suffix}"
            (destination / name).write_text(payload, encoding="utf-8")
            files[name] = {"sha256": digest(payload), "rows": len(items)}
    manifest = {
        "template_id": "tpl-002",
        "task_type": "qa",
        "status": "partial_unavailable",
        "enabled": False,
        "source": {
            "repo": REPO,
            "revision": REVISION,
            "license": "CC-BY-4.0",
            "original_url": "https://data.mendeley.com/datasets/tnj49gpmtz/2",
            "files": source_files,
            "rows": len(records),
            "counts": dict(Counter(r["split"] for r in records)),
        },
        "counts": {s: len(result[s]) for s in SPLITS},
        "dropped": dict(dropped),
        "files": files,
        "tokenizer": {
            "repo": "Qwen/Qwen2.5-1.5B-Instruct",
            "revision": TOKENIZER_REVISION,
            "max_chat_tokens": 2048,
            "truncated": False,
            "files_sha256": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((args.root / "tokenizer").glob("*.json"))
            },
        },
        "coverage": {
            "language": ["en"],
            "fields": ["vendor", "date", "invoice_number", "items", "subtotal", "tax", "total"],
            "documents": "Synthetic invoices",
            "labels": "Model-generated silver, not human gold",
        },
        "limitations": [
            "English only; original Thai/English template remains unavailable.",
            "Synthetic invoices; extraction and visual review are model-generated, not human gold.",
            "No evidence of trained SLM quality; test set is small.",
            "Substring grounding does not certify semantic field assignment.",
            "External validation/test must never be uploaded as training data.",
        ],
        "transformation": "One QA record per invoice; full unchanged OCR only as input; selected source JSON as answer. "
        "All output values require normalized whitespace/case substring grounding. No numeric conversion, translation or truncation. "
        "Connected seller/invoice-number and normalized OCR groups cannot cross splits; original test wins, then validation.",
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    attribution = (source / "docs/ATTRIBUTION.md").read_text()
    (destination / "ATTRIBUTION.md").write_text(
        attribution
        + "\n\n## Prepared derivative\n\nSource snapshot: https://huggingface.co/datasets/"
        + REPO
        + "/tree/"
        + REVISION
        + "\n\nLicense: CC BY 4.0, https://creativecommons.org/licenses/by/4.0/\n\nChanges: selected grounded fields into text-only QA; removed uncertain, duplicate and overlength rows; preserved held-out invoice groups. No new labels or translation. Partial English silver starter; template remains unavailable.\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "template": "tpl-002",
                "status": manifest["status"],
                "counts": manifest["counts"],
                "dropped": dict(dropped),
            }
        )
    )


if __name__ == "__main__":
    main()
