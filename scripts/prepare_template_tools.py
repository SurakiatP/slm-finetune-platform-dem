"""Prepare licensed, pinned tool-calling samples without network or model calls.

Run with a Python environment containing the existing training dependencies:
    python scripts/prepare_template_tools.py --root data/template-catalog
Outputs are deliberately PARTIAL: these sources do not cover full CRUD/alarm APIs.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError

from ai_engine.training.data_formatters import get_formatter
from api.schemas.data_formats import ToolCallingSample
from api.schemas.enums import TaskType

SOURCES = {
    "glaive": {
        "repo": "glaiveai/glaive-function-calling-v2",
        "revision": "e7f4b6456019f5d8bcb991ef0dd67d8ff23221ac",
        "license": "Apache-2.0",
        "files": {
            "glaive-function-calling-v2.json": "e9b5d671812b5ca2fbd7b625a37d5c99a19576c37252cdc806defe256aea6dad"
        },
    },
    "home-assistant": {
        "repo": "acon96/Home-Assistant-Requests-V2",
        "revision": "29ac1a80b7185e7b4c2c9e43b7e4fe71531ec434",
        "license": "MIT",
        "files": {
            "home_assistant_train_english.jsonl": "cbb90f9f83aa36ce4347e5a4861dd33a82d2209738cef7df7520e18655faca6a",
            "home_assistant_test_english.jsonl": "71a5488a9d66e2f6de6693d85882853e4674ac13313173063f32b681106fb4ab",
        },
    },
}
CRUD_NAMES = {
    "create_user",
    "create_user_account",
    "create_user_profile",
    "create_new_user",
    "register_user",
    "get_user_profile",
    "get_user_info",
    "get_user_details",
    "get_user_data",
    "retrieve_user_details",
    "retrieve_user_profile",
    "search_product",
    "search_products",
    "search_for_product",
    "get_product_details",
    "track_order",
    "track_order_status",
}
HOME_NAMES = {
    "HassTurnOn",
    "HassTurnOff",
    "HassToggle",
    "HassLightSet",
    "HassClimateSetTemperature",
}
FORMAT = get_formatter(TaskType.TOOL_CALLING)


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def file_sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def parse_call(raw):
    """Accept JSON or literal legacy dictionaries, never evaluate source code."""
    try:
        call = json.loads(raw)
    except json.JSONDecodeError:
        call = ast.literal_eval(raw)
    arguments = call["arguments"]
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict) or not isinstance(call.get("name"), str):
        raise ValueError("invalid call")
    return {"name": call["name"], "parameters": arguments}


def parse_definitions(system):
    """Original Glaive schemas are consecutive JSON objects after a preamble."""
    decoder = json.JSONDecoder()
    rest = system[system.index("{") :]
    definitions = []
    while rest.strip():
        item, end = decoder.raw_decode(rest.lstrip())
        definitions.append(item)
        rest = rest.lstrip()[end:]
    return definitions


def validate_call(answer, definitions):
    tool = next((item for item in definitions if item["name"] == answer["name"]), None)
    if tool is None:
        raise ValueError("undeclared_tool")
    schema = tool.get("parameters", {})
    Draft7Validator.check_schema(schema)
    if not Draft7Validator(schema).is_valid(answer["parameters"]):
        raise ValueError("invalid_arguments")


def glaive_candidate(record):
    calls = list(
        re.finditer(r"ASSISTANT:\s*<functioncall>\s*(.*?)\s*<\|endoftext\|>", record["chat"], re.S)
    )
    if len(calls) != 1 or record["chat"].count("<functioncall>") != 1:
        raise ValueError("not_single_call")
    answer = parse_call(calls[0][1])
    if answer["name"] not in CRUD_NAMES:
        raise ValueError("outside_crud_subset")
    definitions = parse_definitions(record["system"])
    validate_call(answer, definitions)
    history = record["chat"][: calls[0].start()].strip()
    users = re.findall(r"USER:\s*(.*?)(?=\n\s*ASSISTANT:|$)", history, re.S)
    if not users:
        raise ValueError("no_user")
    question = (
        "Available tools:\n"
        + compact(definitions)
        + "\nConversation:\n"
        + history.replace("<|endoftext|>", "").strip()
    )
    return {"question": question, "answer": compact(answer)}, users[-1]


def text_content(message):
    content = message.get("content", [])
    return (
        content if isinstance(content, str) else "\n".join(part.get("text", "") for part in content)
    )


def home_candidate(record):
    calls = [
        (index, call["function"])
        for index, message in enumerate(record["messages"])
        for call in message.get("tool_calls", []) or []
    ]
    if len(calls) != 1:
        raise ValueError("not_single_call")
    index, source_call = calls[0]
    answer = parse_call(compact(source_call))
    if answer["name"] not in HOME_NAMES:
        raise ValueError("outside_home_subset")
    definitions = [tool["function"] for tool in record["tools"]]
    validate_call(answer, definitions)
    preceding = record["messages"][:index]
    system = "\n".join(text_content(m) for m in preceding if m["role"] == "system")
    # Require an exact source device ID or display name; don't invent alias mappings.
    devices = re.findall(r"^([\w.]+) '(.+?)' =", system, re.M)
    name = answer["parameters"].get("name", "")
    selected = [
        (entity, label)
        for entity, label in devices
        if normalized(name) in (normalized(entity), normalized(label))
    ]
    if len(selected) != 1:
        raise ValueError("device_missing_or_ambiguous")
    if selected[0][0].split(".")[0] not in {"light", "lock", "climate"}:
        raise ValueError("outside_home_devices")
    users = [text_content(m) for m in preceding if m["role"] == "user"]
    if not users:
        raise ValueError("no_user")
    # Keep the full source state and ALL available tools. Never trim by target label.
    history = "\n".join(f"{m['role'].upper()}: {text_content(m)}" for m in preceding)
    return {
        "question": "Available tools:\n" + compact(definitions) + "\nContext:\n" + history,
        "answer": compact(answer),
    }, users[-1]


def prepare(root, tokenizer, source_name, template_id, converter):
    source = SOURCES[source_name]
    drops = Counter()
    eligible = []
    source_rows = {}
    for filename, expected in source["files"].items():
        path = root / "sources" / source_name / filename
        if file_sha(path) != expected:
            raise ValueError(f"Source checksum mismatch: {path}")
        with path.open() as handle:
            records = (
                json.load(handle)
                if path.suffix == ".json"
                else (json.loads(line) for line in handle)
            )
            for index, record in enumerate(records):
                source_rows[filename] = index + 1
                try:
                    row, query = converter(record)
                    ToolCallingSample.model_validate(row)
                except (ValueError, KeyError, TypeError, SyntaxError, SchemaError) as exc:
                    reason = str(exc) if type(exc) is ValueError else "parse_error"
                    drops[
                        reason
                        if reason
                        in {
                            "not_single_call",
                            "outside_crud_subset",
                            "undeclared_tool",
                            "invalid_arguments",
                            "no_user",
                            "outside_home_subset",
                            "device_missing_or_ambiguous",
                            "outside_home_devices",
                        }
                        else "parse_error"
                    ] += 1
                    continue
                tokens = len(
                    tokenizer.apply_chat_template(
                        FORMAT(row), tokenize=True, add_generation_prompt=False
                    )
                )
                if tokens > 2048:
                    drops["over_2048_tokens"] += 1
                    continue
                eligible.append(
                    {
                        "row": row,
                        "source_file": filename,
                        "source_row": index,
                        "source_group": f"{filename}:{index}",
                        "query_sha256": sha(normalized(query)),
                        "input_sha256": sha(normalized(row["question"])),
                        "tokens": tokens,
                    }
                )
    # Deduplicate across splits before assigning. Preserve the official test holdout.
    eligible.sort(
        key=lambda item: (
            0 if "test" in item["source_file"] else 1,
            item["query_sha256"],
            item["input_sha256"],
        )
    )
    seen_queries, seen_inputs = set(), set()
    splits = {"train": [], "validation": [], "test": []}
    caps = {"train": 3000, "validation": 300, "test": 500}
    for item in eligible:
        query, input_hash = item["query_sha256"], item["input_sha256"]
        if query in seen_queries or input_hash in seen_inputs:
            drops["duplicate_query_or_input"] += 1
            continue
        seen_queries.add(query)
        seen_inputs.add(input_hash)
        bucket = int(query[:8], 16) % 100
        if source_name == "home-assistant":
            split = (
                "test"
                if "test" in item["source_file"]
                else "validation"
                if bucket < 10
                else "train"
            )
        else:
            split = "test" if bucket < 15 else "validation" if bucket < 25 else "train"
        if len(splits[split]) >= caps[split]:
            drops["split_cap"] += 1
            continue
        splits[split].append(item)
    output = root / "prepared" / template_id
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "template_id": template_id,
        "status": "PARTIAL",
        "task_type": "tool_calling",
        "language": "en",
        "synthetic_source": True,
        "source": source,
        "source_rows": source_rows,
        "tokenizer": "Qwen/Qwen2.5-1.5B-Instruct",
        "tokenizer_revision": "989aa7980e4cf806f80c7fef2b1adb7bc71aa306",
        "max_sequence_length": 2048,
        "drop_reasons": dict(drops),
        "splits": {},
        "limitations": [
            "Not production-ready for the exact advertised template.",
            "No full CRUD coverage."
            if source_name == "glaive"
            else "No security alarm examples; only lights, locks, and temperature covered.",
            "English only; source data is synthetic, not real user telemetry.",
            "Counts are quality-filtered starting data, not evidence of trained model performance.",
            "Consumers must supply available tool schemas and device state/conversation context at inference.",
        ],
    }
    for split, items in splits.items():
        row_path = output / f"{split}.jsonl"
        provenance_path = output / f"{split}.provenance.jsonl"
        row_path.write_text("".join(compact(item["row"]) + "\n" for item in items))
        provenance_path.write_text(
            "".join(
                compact({key: value for key, value in item.items() if key != "row"}) + "\n"
                for item in items
            )
        )
        manifest["splits"][split] = {
            "rows": len(items),
            "sha256": file_sha(row_path),
            "provenance_sha256": file_sha(provenance_path),
            "max_tokens": max((item["tokens"] for item in items), default=0),
            "tool_distribution": dict(
                Counter(json.loads(item["row"]["answer"])["name"] for item in items)
            ),
        }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (output / "ATTRIBUTION.md").write_text(
        f"# {template_id} dataset attribution\n\n"
        f"Source: https://huggingface.co/datasets/{source['repo']}/tree/{source['revision']}\n\n"
        f"Source dataset card declares {source['license']}. Preserve attribution and license notices on redistribution.\n\n"
        "Changes: filtered original synthetic examples, validated source function schemas, converted arguments to parameters, "
        "deduplicated user queries, and filtered full formatted sequences longer than 2048 tokens. "
        "Original tool names, arguments, schemas, and device state are retained. No translation or generated replacement labels.\n\n"
        "This is a partial research training subset, not evidence that all advertised template capabilities work.\n"
    )
    print(compact({"template_id": template_id, "splits": manifest["splits"], "drops": drops}))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/template-catalog"))
    args = parser.parse_args()
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.root / "tokenizer", local_files_only=True)
    prepare(args.root, tokenizer, "glaive", "tpl-003", glaive_candidate)
    prepare(args.root, tokenizer, "home-assistant", "tpl-007", home_candidate)


if __name__ == "__main__":
    main()
