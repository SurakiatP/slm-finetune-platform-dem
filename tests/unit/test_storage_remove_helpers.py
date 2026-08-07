"""Direct unit tests for `workers/storage.py`'s `remove_object`/`remove_prefix` —
the two orphan-cleanup primitives added for gap-analysis item 13. The task
modules (`data_generation.py`, `training.py`, `model_export.py`) each call
into these from their `except BaseException` handlers; this file pins their
own behaviour in isolation against `fake_minio`, independent of any task.
"""

from __future__ import annotations

from io import BytesIO

from workers.storage import remove_object, remove_prefix


def _put(fake_minio, bucket, key, body=b"x"):
    fake_minio.put_object(bucket, key, data=BytesIO(body), length=len(body))


class TestRemoveObject:
    def test_removes_the_named_object_only(self, fake_minio) -> None:
        _put(fake_minio, "datasets", "sdg/a.jsonl")
        _put(fake_minio, "datasets", "sdg/b.jsonl")

        remove_object(fake_minio, "datasets", "sdg/a.jsonl")

        remaining = [o.object_name for o in fake_minio.list_objects("datasets", recursive=True)]
        assert remaining == ["sdg/b.jsonl"]

    def test_missing_object_is_a_no_op(self, fake_minio) -> None:
        # Nothing exists at this key — must not raise (mirrors
        # `OllamaClient.delete_model`'s idempotent-404 stance, and the real
        # `minio.remove_object` also doesn't error on a missing key).
        remove_object(fake_minio, "datasets", "sdg/does-not-exist.jsonl")


class TestRemovePrefix:
    def test_removes_every_key_under_the_prefix_and_returns_the_count(self, fake_minio) -> None:
        _put(fake_minio, "models", "adapters/abc/adapter_model.bin")
        _put(fake_minio, "models", "adapters/abc/adapter_config.json")
        _put(fake_minio, "models", "adapters/xyz/adapter_model.bin")  # different prefix

        removed = remove_prefix(fake_minio, "models", "adapters/abc")

        assert removed == 2
        remaining = [o.object_name for o in fake_minio.list_objects("models", recursive=True)]
        assert remaining == ["adapters/xyz/adapter_model.bin"]

    def test_does_not_touch_a_similarly_named_sibling_prefix(self, fake_minio) -> None:
        # "adapters/ab" must not match "adapters/abc/..." — remove_prefix
        # appends a trailing "/" before matching, exactly like put_directory
        # appends one before writing, so the two stay symmetric.
        _put(fake_minio, "models", "adapters/abc/adapter_model.bin")

        removed = remove_prefix(fake_minio, "models", "adapters/ab")

        assert removed == 0
        remaining = [o.object_name for o in fake_minio.list_objects("models", recursive=True)]
        assert remaining == ["adapters/abc/adapter_model.bin"]

    def test_empty_prefix_removes_nothing_and_returns_zero(self, fake_minio) -> None:
        removed = remove_prefix(fake_minio, "models", "adapters/never-uploaded")
        assert removed == 0
