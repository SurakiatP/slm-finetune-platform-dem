"""Offline full-chat token check for the actual prepared, curated template data."""

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_ready_templates_fit_with_their_actual_system_prompt():
    if os.getenv("TEMPLATE_DATA_CHECK") != "1":
        pytest.skip("Set TEMPLATE_DATA_CHECK=1 with prepared data and cached tokenizer")
    from transformers import AutoTokenizer

    from ai_engine.training.data_formatters import get_formatter
    from api.schemas.enums import TaskType

    tokenizer = AutoTokenizer.from_pretrained(
        ROOT / "data/template-catalog/tokenizer", local_files_only=True, trust_remote_code=False
    )
    definitions = json.loads((ROOT / "api/template_catalog.json").read_text())
    checked = 0
    for definition in definitions:
        if not definition["data_ready"]:
            continue
        formatter = get_formatter(
            TaskType(definition["task_type"]), system_prompt=definition["prompt"]
        )
        for split in ["train", "validation", "test"]:
            path = ROOT / "data/template-catalog/prepared" / definition["id"] / f"{split}.jsonl"
            maximum = 0
            count = 0
            with path.open() as handle:
                for line in handle:
                    row = json.loads(line)
                    tokens = tokenizer.apply_chat_template(
                        formatter(row), tokenize=True, add_generation_prompt=False
                    )
                    maximum = max(maximum, len(tokens))
                    count += 1
            assert count == definition["split_counts"][split]
            assert maximum <= 2048, (definition["id"], split, maximum)
            print(f"{definition['id']} {split}: {count} rows; full-chat max={maximum}/2048")
            checked += count
    assert checked == 12740
