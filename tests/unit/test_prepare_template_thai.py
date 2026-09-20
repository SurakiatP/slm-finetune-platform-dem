"""Synthetic-only checks for offline Thai template data conversion."""

import json

import pytest

from scripts.prepare_template_thai import (
    NER_INSTRUCTION,
    convert_ner,
    convert_sentiment,
    preferred_partitions,
    prepare_partition,
    select_rows,
    text_hash,
)


def test_ner_spans_preserve_spaces_offsets_and_exclude_unrequested_types():
    names = ["B-PERSON", "I-PERSON", "O", "B-ORGANIZATION", "B-TIME", "B-MONEY"]
    words = ["สมชาย", " ", "ใจดี", " พบ ", "บริษัททดสอบ", " เวลา ", "เที่ยง", " ได้ ", "100บาท"]
    row = convert_ner({"words": words, "ner": [0, 1, 1, 2, 3, 2, 4, 2, 5]}, names)
    text = "".join(words)
    assert row["question"] == NER_INSTRUCTION + text
    entities = json.loads(row["answer"])
    assert [entity["type"] for entity in entities] == ["PERSON", "ORG", "MONEY"]
    assert entities[0]["text"] == "สมชาย ใจดี"
    for entity in entities:
        assert text[entity["start"] : entity["end"]] == entity["text"]
    assert convert_ner({"words": ["ข้อความ"], "ner": [2]}, names)["answer"] == "[]"


@pytest.mark.parametrize(
    "words,ids,names,error",
    [
        (["x"], [0], ["I-PERSON"], "invalid_bio_continuation"),
        (["x", "y"], [0, 1], ["B-PERSON", "I-MONEY"], "invalid_bio_continuation"),
        (["x"], [], ["O"], "unaligned_ner_tokens"),
        ([""], [0], ["O"], "empty_or_invalid_ner_token"),
        (["x"], [0], ["S-PERSON"], "invalid_bio_tag"),
        (["x"], [0], ["B-DTAE"], "invalid_bio_tag"),
        (["x"], [-1], ["O"], "invalid_label_id"),
    ],
)
def test_ner_rejects_bad_tags_and_alignment(words, ids, names, error):
    with pytest.raises(ValueError, match=error):
        convert_ner({"words": words, "ner": ids}, names)


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
    owners = preferred_partitions(partitions, "tpl-006")
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
    selected = select_rows(candidates, "tpl-006", "train")
    assert len(selected) == 9
    assert selected == select_rows(list(reversed(candidates)), "tpl-006", "train")
    assert all(
        sum(item["row"]["label"] == label for item in selected) == 3
        for label in ("positive", "neutral", "negative")
    )
