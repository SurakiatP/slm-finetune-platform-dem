"""Database constraints for durable template registrations, uses, and ratings."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from api.models import Base


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):
    return "JSON"


def test_template_constraints_and_independence_from_project():
    from api.models import Project, TemplateDatasetVersion, TemplateRating, TemplateUse, TrainingJob

    assert Project.__table__.c.template_snapshot.nullable
    assert TrainingJob.__table__.c.owner_id.nullable
    assert TrainingJob.__table__.c.context_snapshot.nullable
    assert not TemplateUse.__table__.c.project_id.foreign_keys
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        registration = TemplateDatasetVersion(
            template_id="tpl-006",
            version="v1",
            definition_sha256="a" * 64,
            manifest_json={},
            splits_json={},
        )
        db.add(registration)
        db.add(
            TemplateUse(
                user_id="alice",
                template_id="tpl-006",
                template_version="v1",
                idempotency_key="request-1",
                request_sha256="b" * 64,
                project_id=uuid4(),
                response_json={"created": True},
            )
        )
        db.add(TemplateRating(template_id="tpl-006", user_id="alice", rating=5))
        db.commit()
        assert db.get(TemplateDatasetVersion, ("tpl-006", "v1")) is registration

        db.add(
            TemplateUse(
                user_id="alice",
                template_id="tpl-006",
                template_version="v2",
                idempotency_key="request-1",
                request_sha256="c" * 64,
                project_id=uuid4(),
                response_json={},
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        for rating in [0, 6]:
            db.add(TemplateRating(template_id="tpl-006", user_id="bob", rating=rating))
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
        db.add(TemplateRating(template_id="tpl-006", user_id="alice", rating=4))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    engine.dispose()
