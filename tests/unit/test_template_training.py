"""Template training contracts; CPU tests, with GPU dependencies replaced at the boundary."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from ai_engine.training.data_formatters import get_formatter
from api.core.auth import CurrentUser
from api.models.base import Base
from api.models.dataset import Dataset
from api.models.project import Project
from api.models.training_job import TrainingJob
from api.schemas.enums import DatasetSource, JobStatus, TaskType
from api.schemas.training import HPOTrainingRequest, ManualTrainingRequest
from api.services import training_service

MODEL = "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit"
USER = CurrentUser(id="template-trainer", email="trainer@example.test")


@compiles(JSONB, "sqlite")
def _jsonb(element, compiler, **kwargs):
    return "JSON"


@pytest.fixture
async def db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    from workers.tasks.hpo_training import train_hpo
    from workers.tasks.training import train_manual

    for task in (train_manual, train_hpo):
        monkeypatch.setattr(task, "apply_async", lambda **kw: SimpleNamespace(id=str(uuid4())))

    async def no_quota(*args, **kwargs):
        pass

    monkeypatch.setattr(training_service.quota, "assert_can_submit", no_quota)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def seed(db):
    project = Project(id=uuid4(), name="template", task_type=TaskType.QA, owner_id=USER.id)
    db.add(project)
    await db.flush()
    snapshot = dict(
        template_id="tpl-004",
        template_version="1",
        base_model=MODEL,
        system_prompt="Extract entities exactly.",
        chat_template="chatml",
        manual_config={"num_train_epochs": 2, "learning_rate": 0.0001},
        train_sample_count=5,
        sampling_seed=7,
    )
    datasets = {}
    for role, count in (("train", 10), ("validation", 3), ("test", 2)):
        row = Dataset(
            id=uuid4(),
            name=role,
            project_id=project.id,
            owner_id=USER.id,
            task_type=TaskType.QA,
            source=DatasetSource.UPLOADED,
            status=JobStatus.COMPLETED,
            num_samples=count,
            storage_uri=f"s3://datasets/{role}.jsonl",
            generation_metadata=dict(
                template_id="tpl-004", template_version="1", role=role, sha256=role[0] * 64
            ),
        )
        db.add(row)
        datasets[role] = row
        snapshot[f"{role}_dataset_id"] = str(row.id)
        snapshot[f"{role}_sha256"] = role[0] * 64
    project.template_snapshot = snapshot
    await db.commit()
    return project, datasets


def request_for(mode, project, datasets, **overrides):
    kwargs = dict(project_id=project.id, dataset_id=datasets["train"].id, **overrides)
    if mode == "hpo":
        kwargs.setdefault(
            "hpo_config",
            {"search_space": {"learning_rate": {"type": "float", "low": 0.00001, "high": 0.0002}}},
        )
        return HPOTrainingRequest(**kwargs)
    return ManualTrainingRequest(**kwargs)


async def submit(db, request):
    fn = (
        training_service.submit_manual_training_job
        if isinstance(request, ManualTrainingRequest)
        else training_service.submit_hpo_training_job
    )
    result = await fn(db, request, USER)
    return await db.get(TrainingJob, result.training_id)


@pytest.mark.parametrize("mode", ["manual", "hpo"])
async def test_template_defaults_are_frozen_and_owned(db, mode):
    project, datasets = await seed(db)
    job = await submit(db, request_for(mode, project, datasets))
    assert job.owner_id == USER.id
    assert job.base_model == MODEL
    assert job.context_snapshot["system_prompt"] == "Extract entities exactly."
    config = job.config_json if mode == "manual" else job.config_json["fixed_config"]
    assert config["num_train_epochs"] == 2
    assert config["learning_rate"] == 0.0001
    frozen = deepcopy(job.context_snapshot)
    project.template_snapshot = dict(project.template_snapshot, system_prompt="Changed")
    await db.commit()
    await db.refresh(job)
    assert job.context_snapshot == frozen
    assert "system_prompt" not in job.config_json


@pytest.mark.parametrize("mode", ["manual", "hpo"])
async def test_explicit_partial_config_overrides_defaults(db, mode):
    project, datasets = await seed(db)
    override = (
        {"manual_config": {"num_train_epochs": 4}}
        if mode == "manual"
        else {
            "hpo_config": {
                "search_space": {
                    "learning_rate": {"type": "float", "low": 0.00001, "high": 0.0002}
                },
                "fixed_config": {"num_train_epochs": 4},
            }
        }
    )
    job = await submit(
        db,
        request_for(
            mode,
            project,
            datasets,
            system_prompt="Custom",
            train_sample_count=3,
            sampling_seed=123,
            **override,
        ),
    )
    assert job.context_snapshot["manual_config"]["num_train_epochs"] == 4
    assert job.context_snapshot["manual_config"]["learning_rate"] == 0.0001
    assert job.context_snapshot["system_prompt"] == "Custom"
    assert job.context_snapshot["train_sample_count"] == 3
    assert job.context_snapshot["sampling_seed"] == 123


@pytest.mark.parametrize("mode", ["manual", "hpo"])
@pytest.mark.parametrize("role", ["validation", "test"])
async def test_heldout_cannot_train_in_an_ordinary_project(db, mode, role):
    project, datasets = await seed(db)
    project.template_snapshot = None
    request = request_for(mode, project, {"train": datasets[role]})
    with pytest.raises(HTTPException) as error:
        await submit(db, request)
    assert error.value.status_code == 422


@pytest.mark.parametrize("corruption", ["owner", "hash", "status", "task"])
async def test_validation_must_still_match_snapshot(db, corruption):
    project, datasets = await seed(db)
    row = datasets["validation"]
    if corruption == "owner":
        row.owner_id = "another-user"
    if corruption == "hash":
        row.generation_metadata = dict(row.generation_metadata, sha256="bad")
    if corruption == "status":
        row.status = JobStatus.FAILED
    if corruption == "task":
        row.task_type = TaskType.CLASSIFICATION
    await db.commit()
    with pytest.raises(HTTPException):
        await submit(db, request_for("manual", project, datasets))


async def test_named_owner_keeps_name_after_project_deleted(db):
    project, datasets = await seed(db)
    job = await submit(db, request_for("manual", project, datasets, training_name="retained"))
    job.project_id = None
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await submit(db, request_for("manual", project, datasets, training_name="retained"))
    assert error.value.status_code == 409


async def test_explicit_model_prompt_and_all_rows_override(db):
    project, datasets = await seed(db)
    model = "unsloth/Llama-3.2-1B-Instruct-bnb-4bit"
    job = await submit(
        db,
        request_for(
            "manual",
            project,
            datasets,
            base_model=model,
            system_prompt=None,
            train_sample_count=None,
            sampling_seed=0,
        ),
    )
    assert job.base_model == model
    assert job.context_snapshot["chat_template"] == "llama-3.2"
    assert job.context_snapshot["system_prompt"] is None
    assert job.context_snapshot["train_sample_count"] == 10
    assert job.context_snapshot["sampling_seed"] == 0


@pytest.mark.parametrize(
    "override", [{"base_model": "unapproved/model"}, {"train_sample_count": 11}]
)
async def test_invalid_model_or_sample_count_fails_before_dispatch(db, override):
    project, datasets = await seed(db)
    with pytest.raises(HTTPException) as error:
        await submit(db, request_for("manual", project, datasets, **override))
    assert error.value.status_code == 422


async def test_hpo_fixed_config_respects_effective_model_memory_limit(db):
    project, datasets = await seed(db)
    request = request_for(
        "hpo",
        project,
        datasets,
        hpo_config={
            "search_space": {"learning_rate": {"type": "float", "low": 0.00001, "high": 0.0002}},
            "fixed_config": {"per_device_train_batch_size": 16},
        },
    )
    with pytest.raises(HTTPException) as error:
        await submit(db, request)
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "task,row",
    [
        (TaskType.QA, {"question": "Q", "answer": "A"}),
        (TaskType.CLASSIFICATION, {"text": "T", "label": "L"}),
        (TaskType.TOOL_CALLING, {"question": "Q", "answer": "{}"}),
    ],
)
def test_custom_prompt_replaces_only_system_message(task, row):
    messages = get_formatter(task, system_prompt="Custom")(row)
    assert messages[0] == {"role": "system", "content": "Custom"}
    assert len(messages) == 3
    assert messages[1:] == [m for m in get_formatter(task)(row) if m["role"] != "system"]


def test_render_checks_complete_prompt_and_answer_before_truncation():
    from ai_engine.training.unsloth_trainer import _render_rows

    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return "|".join(message["content"] for message in messages)

        def __call__(self, text, **kwargs):
            assert kwargs == {"add_special_tokens": False, "truncation": False}
            return {"input_ids": list(range(len(text)))}

    for role in ("train", "validation"):
        with pytest.raises(ValueError, match=f"{role} row 1.*max_seq_length"):
            _render_rows(
                Tokenizer(),
                get_formatter(TaskType.QA, system_prompt="LONG PROMPT"),
                [{"question": "Q", "answer": "LONG ANSWER"}],
                max_seq_length=10,
                role=role,
            )


def test_verified_sampling_preserves_classes_seed_and_validation(fake_minio):
    import hashlib
    import json

    from workers.storage import put_jsonl
    from workers.tasks.training import _prepare_training_rows

    rows = [{"text": str(i), "label": "a" if i < 8 else "b"} for i in range(10)]
    validation = [{"text": "external-only", "label": "a"}]
    context = dict(
        template_id="tpl-006",
        train_sample_count=5,
        sampling_seed=9,
        train_storage_uri="s3://datasets/train.jsonl",
    )
    for role, values in (("train", rows), ("validation", validation)):
        put_jsonl(fake_minio, "datasets", f"{role}.jsonl", values)
        body = "\n".join(json.dumps(row, ensure_ascii=False) for row in values).encode()
        context[f"{role}_sha256"] = hashlib.sha256(body).hexdigest()
    args = (fake_minio, rows, TaskType.CLASSIFICATION, context, "s3://datasets/validation.jsonl")
    reduced, heldout = _prepare_training_rows(*args)
    assert len(reduced) == 5
    assert sum(row["label"] == "a" for row in reduced) == 4
    assert _prepare_training_rows(*args) == (reduced, validation)
    assert heldout == validation
    context["train_sample_count"] = len(rows)
    assert _prepare_training_rows(*args)[0] == rows
    put_jsonl(fake_minio, "datasets", "train.jsonl", [rows[0]])
    with pytest.raises(RuntimeError, match="SHA256"):
        _prepare_training_rows(*args)


def test_external_validation_uses_all_train_and_selects_checkpoint(monkeypatch, tmp_path):
    import sys

    from ai_engine.training.unsloth_trainer import UnslothTrainer
    from api.schemas.training import ManualTrainingConfig

    captures = {}

    class Tokenizer:
        eos_token = "EOS"

        def get_vocab(self):
            return {"EOS": 0}

        def apply_chat_template(self, messages, **kwargs):
            return "|".join(message["content"] for message in messages)

        def __call__(self, text, **kwargs):
            return {"input_ids": list(range(len(text)))}

        def save_pretrained(self, path):
            pass

    class FakeDataset(list):
        @classmethod
        def from_list(cls, rows):
            return cls(rows)

        def train_test_split(self, **kwargs):
            captures["split"] = kwargs
            return {"train": self[:-1], "test": self[-1:]}

    class FakeSFT:
        def __init__(self, **kwargs):
            captures.update(kwargs)

        def train(self):
            return SimpleNamespace(metrics={"train_loss": 1.0}, global_step=1)

        def evaluate(self):
            return {"eval_loss": 0.5}

        def save_model(self, path):
            pass

    model = SimpleNamespace(
        from_pretrained=lambda **kwargs: (object(), Tokenizer()),
        get_peft_model=lambda model, **kwargs: model,
    )
    monkeypatch.setitem(sys.modules, "unsloth", SimpleNamespace(FastLanguageModel=model))
    monkeypatch.setitem(
        sys.modules,
        "unsloth.chat_templates",
        SimpleNamespace(get_chat_template=lambda tokenizer, **kwargs: tokenizer),
    )
    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(Dataset=FakeDataset))
    monkeypatch.setitem(
        sys.modules,
        "trl",
        SimpleNamespace(SFTConfig=lambda **kwargs: SimpleNamespace(**kwargs), SFTTrainer=FakeSFT),
    )
    trainer = UnslothTrainer(
        base_model=MODEL,
        config=ManualTrainingConfig(),
        task_type=TaskType.QA,
        system_prompt="Custom",
        output_dir=str(tmp_path),
    )
    rows = [{"question": str(i), "answer": "train-only"} for i in range(6)]
    validation = [{"question": "V", "answer": "validation-only"}]
    result = trainer.train(rows, validation_rows=validation)
    assert "split" not in captures
    assert len(captures["train_dataset"]) == len(rows)
    assert captures["eval_dataset"] == [{"text": "Custom|V|validation-onlyEOS"}]
    assert captures["args"].load_best_model_at_end is True
    assert captures["args"].metric_for_best_model == "eval_loss"
    assert result.final_eval_loss == 0.5
    trainer.train(rows)
    assert captures["split"]["seed"] == 42
    assert len(captures["train_dataset"]) == 5
