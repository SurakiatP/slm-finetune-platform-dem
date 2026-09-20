"""Prepare pinned TechQA as context-grounded QA, never inventing answers."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_engine.training.data_formatters import format_qa
from api.schemas.data_formats import QASample

REVISION = "0b5bbc84b7f07d6d09d063130e90b716d8d4a32a"
TOKENIZER_REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
SOURCE_SHA256 = "69d97231509482ed6bd5ec1c4bc0607acb82a88d11169eb8383592d0ca8b93c7"


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def prepare_rows(rows, token_count):
    """Return canonical rows plus provenance; group shared documents before sampling."""
    output = {split: [] for split in ("train", "validation", "test")}
    dropped = Counter()
    parents = {}

    def find(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    documents = []
    for row in rows:
        keys = set()
        for context in row["contexts"]:
            keys.add("text:" + digest(normalized(context["text"])))
            if context.get("filename"):
                keys.add("name:" + context["filename"])
        for key in sorted(keys):
            parents[find(key)] = find(min(keys))
        documents.append(keys)
    groups = [find(min(keys)) if keys else "empty" for keys in documents]
    heldout = {group for row, group in zip(rows, groups, strict=True) if row["id"].startswith("DEV_")}
    seen_questions = set()
    order = sorted(
        range(len(rows)), key=lambda i: (not rows[i]["id"].startswith("DEV_"), rows[i]["id"])
    )
    for index in order:
        row, group = rows[index], groups[index]
        source_id = row["id"]
        if not source_id.startswith(("TRAIN_", "DEV_")):
            dropped["unknown_split"] += 1
            continue
        if row.get("is_impossible") or row["answer"].strip() in ("", "-") or not row["contexts"]:
            dropped["unanswerable"] += 1
            continue
        if source_id.startswith("TRAIN_") and group in heldout:
            dropped["heldout_document"] += 1
            continue
        query_hash = digest(normalized(row["question"]))
        if query_hash in seen_questions:
            dropped["duplicate_question"] += 1
            continue
        seen_questions.add(query_hash)
        context = "\n\n".join(item["text"] for item in row["contexts"])
        sample = {
            "question": "Answer using the supplied documentation.\n\nDocumentation:\n"
            + context
            + "\n\nQuestion: "
            + row["question"],
            "answer": row["answer"],
        }
        QASample.model_validate(sample)
        tokens = token_count(format_qa(sample))
        if tokens > 2048:
            dropped["over_token_limit"] += 1
            continue
        split = (
            "train"
            if source_id.startswith("TRAIN_")
            else ("validation" if int(digest(group)[:8], 16) % 2 else "test")
        )
        output[split].append(
            (
                sample,
                {
                    "source_id": source_id,
                    "document_group": digest(group),
                    "query_sha256": query_hash,
                    "tokens": tokens,
                },
            )
        )
    return output, dropped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/template-catalog"))
    args = parser.parse_args()
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        args.root / "tokenizer", local_files_only=True, trust_remote_code=False
    )
    source = args.root / "sources/techqa/train.json"
    source_bytes = source.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != SOURCE_SHA256:
        raise ValueError("TechQA source differs from the reviewed pinned snapshot")
    rows, dropped = prepare_rows(
        json.loads(source_bytes),
        lambda messages: len(
            tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False)
        ),
    )
    destination = args.root / "prepared/tpl-005"
    destination.mkdir(parents=True, exist_ok=True)
    files = {}
    for split, items in rows.items():
        for suffix, contents in (
            ("jsonl", [item[0] for item in items]),
            ("provenance.jsonl", [item[1] for item in items]),
        ):
            path = destination / f"{split}.{suffix}"
            payload = "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in contents
            )
            path.write_text(payload, encoding="utf-8")
            files[path.name] = {"rows": len(contents), "sha256": digest(payload)}
    manifest = {
        "template_id": "tpl-005",
        "task_type": "qa",
        "status": "starter_domain_limited",
        "source": {
            "repo": "nvidia/TechQA-RAG-Eval",
            "revision": REVISION,
            "license": "Apache-2.0",
            "url": "https://huggingface.co/datasets/nvidia/TechQA-RAG-Eval/tree/" + REVISION,
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "rows": 910,
        },
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
        "counts": {split: len(items) for split, items in rows.items()},
        "dropped": dict(dropped),
        "files": files,
        "limitations": [
            "English IBM technical support only; not the user's private knowledge base.",
            "Unanswerable rows removed; no abstention training.",
            "Small starter corpus, not evidence of achieved model quality.",
            "External validation/test files must never be uploaded as training data.",
        ],
        "transformation": "Original full documents and question as input; unchanged original answer. "
        "Shared document filename/content components never cross splits. Original DEV "
        "components split deterministically into validation/test. No truncation.",
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    (destination / "SOURCE_LICENSE.md").write_text(
        "# Attribution and changes\n\nNVIDIA TechQA-RAG-Eval, derived from IBM TechQA.\n"
        "Source: https://huggingface.co/datasets/nvidia/TechQA-RAG-Eval/tree/" + REVISION + "\n\n"
        "License declared by source: Apache-2.0; https://www.apache.org/licenses/LICENSE-2.0\n"
        "Keep the downloaded source README with any redistribution and verify upstream notices.\n\n"
        "Changes: concatenated supplied contexts and question, retained original answers, filtered "
        "unanswerable/overlength/duplicate records, document-disjoint deterministic splits. "
        "No answer generation, truncation or translation.\n",
        encoding="utf-8",
    )
    print(
        json.dumps({"template": "tpl-005", "counts": manifest["counts"], "dropped": dict(dropped)})
    )


if __name__ == "__main__":
    main()
