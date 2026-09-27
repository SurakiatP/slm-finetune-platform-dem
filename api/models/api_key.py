"""ApiKey ORM model — a per-user long-lived credential for `sk-slm-...` keys.

Only the SHA-256 hash of the key is stored; the plaintext is shown once at
creation time and never persisted (see api_keys_service in a later wave).
Revocation is soft (`revoked_at` set, row kept) so audit history survives.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, TimestampMixin, uuid_pk


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[UUID] = uuid_pk()
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
