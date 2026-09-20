"""Synthetic-only checks for offline Thai template data conversion."""

import pytest

from scripts.prepare_template_thai import (
    convert_sentiment,
    preferred_partitions,
    prepare_partition,
    select_rows,
    text_hash,
)


def test_sentiment_maps_only_three_classes():
    names = ["pos", "neu", "neg", "q"]
    assert [
        convert_sentiment({"texts": "ข้อความ", "category": index}, names)["label"]
        for index in range(3)
    ] == ["positive", "neutral", "negative"]
    with pytest.raises(ValueError, match="question_class"):
        convert_sentiment({"texts": "คำถาม", "category": 3}, names)


class Tokenizer:
    def __init__(self, length=20):
        self.length = length

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        assert tokenize is True and add_generation_prompt is False
        assert messages[-1]["role"] == "assistant"
        return [1] * self.length


def test_raw_test_partition_reserved_even_when_its_row_is_rejected():
    partitions = {
        "train": [{"texts": " ＨＥＬＬＯ  world ", "category": 0}],  # noqa: RUF001 -- tests NFKC
        "validation": [{"texts": "hello world", "category": 1}],
        "test": [{"texts": "Hello World", "category": 3}],
    }
    owners = preferred_partitions(partitions)
    assert set(owners.values()) == {"test"}
    for split in partitions:
        candidates, drops = prepare_partition(
            partitions[split],
            split=split,
            template_id="tpl-006",
            names=["pos", "neu", "neg", "q"],
            owners=owners,
            tokenizer=Tokenizer(),
        )
        assert not candidates
        assert sum(drops.values()) == 1


def test_token_limit_includes_answer_and_rejects_instead_of_truncating():
    rows = [{"texts": "ข้อความ", "category": 0}]
    options = dict(
        split="train", template_id="tpl-006", names=["pos"], owners={text_hash("ข้อความ"): "train"}
    )
    candidates, _ = prepare_partition(rows, tokenizer=Tokenizer(2048), **options)
    assert candidates[0]["row"] == {"text": "ข้อความ", "label": "positive"}
    candidates, drops = prepare_partition(rows, tokenizer=Tokenizer(2049), **options)
    assert candidates == [] and drops == {"over_2048_tokens": 1}


def test_sampling_balanced_deterministic_and_reports_available_not_fabricated_rows():
    candidates = [
        {"source_id": f"train:{index}:{label}", "row": {"label": label}}
        for label in ("positive", "neutral", "negative")
        for index in range(3)
    ]
    candidates.append({"source_id": "train:extra", "row": {"label": "positive"}})
    selected = select_rows(candidates, "train")
    assert len(selected) == 9
    assert selected == select_rows(list(reversed(candidates)), "train")
    assert all(
        sum(item["row"]["label"] == label for item in selected) == 3
        for label in ("positive", "neutral", "negative")
    )
