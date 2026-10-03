"""Resolve an OIDC identity `(issuer, subject)` to its Engine actor id.

Linked identity -> its stored `actor_id` (e.g. a migrated user's old
Supabase `sub`, so they keep seeing their pre-migration rows). Unseen
identity -> a fresh random `actor_id` is issued and stored, so a new
Keycloak user starts empty and can never land on someone else's owner id —
not even one whose value happens to equal their `sub`, since the Engine DB,
not the provider, decides every actor id.

Links to an existing actor are written only by the operator script
`scripts/link_identity.py`, after confirming out of band that one person
holds both accounts. Nothing here links by email.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.identity_link import IdentityLink


async def resolve_actor(db: AsyncSession, issuer: str, subject: str) -> str:
    stmt = select(IdentityLink.actor_id).where(
        IdentityLink.issuer == issuer, IdentityLink.subject == subject
    )
    actor_id = (await db.execute(stmt)).scalar_one_or_none()
    if actor_id is not None:
        return actor_id

    db.add(IdentityLink(issuer=issuer, subject=subject, actor_id=str(uuid4())))
    try:
        await db.commit()
    except IntegrityError:
        # A concurrent first request for the same identity won the insert;
        # use its row so both requests agree on one actor id.
        await db.rollback()
    return (await db.execute(stmt)).scalar_one()


__all__ = ["resolve_actor"]
