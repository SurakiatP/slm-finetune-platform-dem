"""inference_caller must hand a real session to current_user_optional.

Calling the dependency function directly skips FastAPI injection, so an
omitted `db` stays a `Depends(...)` object and any OIDC token crashed with
AttributeError (500) when Keycloak identity_links needed the session.
"""
from unittest.mock import AsyncMock, patch

import pytest

from api.core.auth import CurrentUser
from api.routers import inference


@pytest.mark.asyncio
async def test_jwt_path_passes_db_session():
    db = object()
    user = CurrentUser(id="actor", email=None)
    with patch.object(inference, "current_user_optional", AsyncMock(return_value=user)) as m:
        caller = await inference.inference_caller(db, "Bearer a.b.c")
    m.assert_awaited_once_with("Bearer a.b.c", db)
    assert caller.user == user
