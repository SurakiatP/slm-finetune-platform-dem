"""Preparation must not leak held-out source documents or truncate answers."""

from scripts.prepare_template_qa import prepare_rows


def example(identifier, document, question="Question", answer="Full answer"):
    return {
        "id": identifier,
        "question": question,
        "answer": answer,
        "is_impossible": False,
        "contexts": [{"filename": document, "text": "Evidence " + document}],
    }


def test_preserves_evidence_and_answer_rejects_long_and_heldout_documents():
    rows = [
        example("TRAIN_1", "shared"),
        example("DEV_1", "shared"),
        example("TRAIN_2", "unique", "Different question"),
        example("TRAIN_3", "long", "Long question", "x" * 3000),
        example("TRAIN_4", "empty", "No answer", "-"),
    ]
    result, dropped = prepare_rows(rows, lambda messages: sum(len(m["content"]) for m in messages))
    assert [item[1]["source_id"] for item in result["train"]] == ["TRAIN_2"]
    assert result["train"][0][0]["answer"] == "Full answer"
    assert "Evidence" in result["train"][0][0]["question"]
    assert dropped["heldout_document"] == 1
    assert dropped["over_token_limit"] == 1
    assert dropped["unanswerable"] == 1
    assert sum(len(result[s]) for s in ("validation", "test")) == 1


def test_duplicate_question_does_not_cross_splits():
    rows = [
        example("TRAIN_1", "a", " Same  question "),
        example("DEV_1", "b", "same question"),
        example("DEV_2", "b", "another question"),
    ]
    result, dropped = prepare_rows(rows, lambda messages: 10)
    assert result["train"] == []
    assert dropped["duplicate_question"] == 1
    assert sorted(len(result[s]) for s in ("validation", "test")) == [0, 2]
