"""Unit tests for workers.notifications.record_notification (sync sqlite)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from api.models.base import Base
from api.models.notification import Notification
from api.models.project import Project
from api.schemas.enums import TaskType
from workers.notifications import record_notification


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


def _rec(session: Session, **over) -> None:
    kw = dict(
        owner_id="u1",
        event_type="training.completed",
        resource_type="training_job",
        resource_id="r1",
        payload={"x": 1},
    )
    kw.update(over)
    record_notification(session, **kw)


def _all(session: Session) -> list[Notification]:
    return list(session.scalars(select(Notification)))


def test_duplicate_is_deduped(engine) -> None:
    with Session(engine) as s:
        _rec(s)
        _rec(s)
        s.commit()
        assert len(_all(s)) == 1


def test_no_owner_is_noop(engine) -> None:
    with Session(engine) as s:
        _rec(s, owner_id=None)
        s.commit()
        assert _all(s) == []


def test_failure_is_contained() -> None:
    eng = create_engine("sqlite://")
    tables = [t for t in Base.metadata.sorted_tables if t.name != "notifications"]
    Base.metadata.create_all(eng, tables=tables)
    with Session(eng) as s:
        project = Project(id=uuid4(), name="p", task_type=TaskType.QA)
        s.add(project)
        s.flush()
        project.name = "renamed"
        _rec(s)  # notifications table missing -> swallowed
        s.commit()
        assert s.get(Project, project.id).name == "renamed"


def test_message_truncated(engine) -> None:
    with Session(engine) as s:
        _rec(s, payload={"message": "m" * 500})
        s.commit()
        assert len(_all(s)[0].payload["message"]) == 300


def test_uuid_payload_stored_as_str(engine) -> None:
    pid = uuid4()
    with Session(engine) as s:
        _rec(s, payload={"project_id": pid})
        s.commit()
        assert _all(s)[0].payload["project_id"] == str(pid)


def test_defaults_populated(engine) -> None:
    with Session(engine) as s:
        _rec(s)
        s.commit()
        n = _all(s)[0]
        assert isinstance(n.id, UUID)
        assert n.created_at is not None
        assert n.read_at is None
