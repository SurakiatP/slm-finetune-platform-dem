"""IdentityLink ORM model — maps an external login to the Engine actor id.

An external identity is the pair `(issuer, subject)`, never `sub` alone.
`actor_id` is what lands in `CurrentUser.id` and therefore in every
`owner_id` / `user_id` column. Only OIDC (Keycloak) logins are resolved
through this table; Supabase logins keep their `sub` as the actor id, which
is what all pre-migration rows are owned by. See `api/services/identity_links.py`.
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, TimestampMixin


class IdentityLink(Base, TimestampMixin):
    __tablename__ = "identity_links"

    issuer: Mapped[str] = mapped_column(String(255), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    # Same width as every owner_id/user_id column it is compared against.
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
