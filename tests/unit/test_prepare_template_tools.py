"""Guard source semantics: no guessed calls, lost context, or malformed targets."""

import json

import pytest

from scripts import prepare_template_tools as prep
from scripts.prepare_template_tools import glaive_candidate, home_candidate, parse_call


def test_glaive_keeps_schemas_and_history_and_validates_arguments():
    definition = {
        "name": "create_user",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    }
    record = {
        "system": "Functions: " + json.dumps(definition),
        "chat": 'USER: Create account\nASSISTANT: Name? <|endoftext|>\nUSER: Alice\nASSISTANT: <functioncall> {"name":"create_user","arguments":{"name":"Alice"}} <|endoftext|>',
    }
    row, query = glaive_candidate(record)
    assert query == "Alice"
    assert "Name?" in row["question"] and "required" in row["question"]
    assert json.loads(row["answer"]) == {"name": "create_user", "parameters": {"name": "Alice"}}
    record["chat"] = record["chat"].replace('{"name":"Alice"}', "{}")
    with pytest.raises(ValueError, match="invalid_arguments"):
        glaive_candidate(record)


def test_home_requires_declared_tool_and_actual_unambiguous_device():
    record = {
        "tools": [{"function": {"name": "HassTurnOn", "parameters": {"type": "object"}}}],
        "messages": [
            {"role": "system", "content": "Devices:\nlight.desk 'Desk Light' = off"},
            {"role": "user", "content": "Turn on the desk light"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "HassTurnOn", "arguments": '{"name":"Desk Light"}'}}
                ],
            },
        ],
    }
    row, _ = home_candidate(record)
    assert "light.desk" in row["question"]
    record["messages"][-1]["tool_calls"][0]["function"]["arguments"] = '{"name":"Invented Light"}'
    with pytest.raises(ValueError, match="device_missing_or_ambiguous"):
        home_candidate(record)
    record["messages"][-1]["tool_calls"] *= 2
    with pytest.raises(ValueError, match="not_single_call"):
        home_candidate(record)


def test_legacy_argument_string_is_parsed_without_execution():
    assert parse_call("""{"name":"create_user","arguments":'{"name":"Alice"}'}""")[
        "parameters"
    ] == {"name": "Alice"}
    with pytest.raises((ValueError, SyntaxError)):
        parse_call("__import__('os').system('echo no')")


def test_duplicate_query_stays_only_in_original_test_split(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "home-assistant"
    source.mkdir(parents=True)
    filenames = ["home_assistant_train_english.jsonl", "home_assistant_test_english.jsonl"]
    for filename in filenames:
        (source / filename).write_text('{"query":"Turn on light"}\n')
    monkeypatch.setitem(
        prep.SOURCES,
        "home-assistant",
        {
            "repo": "example/home",
            "revision": "test",
            "license": "MIT",
            "files": {name: prep.file_sha(source / name) for name in filenames},
        },
    )

    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            assert messages[-1]["content"] == '{"name":"light","parameters":{}}'
            return [1] * 2048

    def convert(record):
        return {"question": record["query"], "answer": '{"name":"light","parameters":{}}'}, record[
            "query"
        ]

    manifest = prep.prepare(tmp_path, Tokenizer(), "home-assistant", "tpl-test", convert)
    assert manifest["splits"]["test"]["rows"] == 1
    assert manifest["splits"]["train"]["rows"] == 0
    assert manifest["splits"]["validation"]["rows"] == 0
    assert manifest["drop_reasons"]["duplicate_query_or_input"] == 1

    class LongTokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return [1] * 2049

    manifest = prep.prepare(tmp_path, LongTokenizer(), "home-assistant", "tpl-long", convert)
    assert all(split["rows"] == 0 for split in manifest["splits"].values())
    assert manifest["drop_reasons"]["over_2048_tokens"] == 2
