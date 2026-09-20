"""Real PostgreSQL migration proof; opt in only against our disposable stack."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command

pytestmark = pytest.mark.integration


def test_owner_backfill_keeps_unknown_orphans_closed(monkeypatch):
    url = os.getenv("TEMPLATE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEMPLATE_TEST_DATABASE_URL for the dedicated local stack")
    parsed = make_url(url)
    assert (parsed.host, parsed.port, parsed.database) == ("127.0.0.1", 15432, "template_tests"), (
        "Refuse migration tests against any other database"
    )
    admin = create_engine(
        parsed.set(drivername="postgresql+psycopg2"), isolation_level="AUTOCOMMIT"
    )
    database = "template_migration_" + uuid4().hex
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
    target = parsed.set(drivername="postgresql+psycopg2", database=database)
    engine = create_engine(target)
    try:
        monkeypatch.setenv("ALEMBIC_DATABASE_URL", target.render_as_string(hide_password=False))
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        command.upgrade(config, "0013_dataset_reuse")
        project_id, dataset_id, known_id, unknown_id = [uuid4() for _ in range(4)]
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id,name,task_type,owner_id) "
                    "VALUES (:id,'migration fixture','classification','fixture-owner')"
                ),
                {"id": project_id},
            )
            connection.execute(
                text(
                    "INSERT INTO datasets (id,project_id,name,task_type,source) "
                    "VALUES (:id,:project,'fixture','classification','uploaded')"
                ),
                {"id": dataset_id, "project": project_id},
            )
            for training_id, parent in [(known_id, project_id), (unknown_id, None)]:
                connection.execute(
                    text(
                        "INSERT INTO training_jobs (id,project_id,dataset_id,mode,base_model,config_json) "
                        "VALUES (:id,:project,:dataset,'manual','fixture','{}')"
                    ),
                    {"id": training_id, "project": parent, "dataset": dataset_id},
                )
        command.upgrade(config, "head")
        with engine.begin() as connection:
            owners = dict(connection.execute(text("SELECT id,owner_id FROM training_jobs")).all())
            assert owners == {known_id: "fixture-owner", unknown_id: None}
            connection.execute(text("DELETE FROM projects WHERE id=:id"), {"id": project_id})
            retained = connection.execute(
                text("SELECT owner_id,project_id FROM training_jobs WHERE id=:id"), {"id": known_id}
            ).one()
            assert retained == ("fixture-owner", None)
            tables = set(
                connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                ).scalars()
            )
            assert {"template_dataset_versions", "template_uses", "template_ratings"} <= tables
        command.downgrade(config, "0013_dataset_reuse")
        command.upgrade(config, "head")
    finally:
        engine.dispose()
        # The generated, validated identifier belongs only to this test.
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database}"'))
        admin.dispose()
