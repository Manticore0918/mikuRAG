import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.admin import delete_knowledge_base
from app.database import get_session
from app.dependencies import ensure_knowledge_base_access, get_current_user
from app.main import app
from app.models import KnowledgeBase, User
from app.security import create_session_token


class FakeSession:
    def __init__(self, scalar_value=False, rows=(), get_value=None):
        self.scalar_value = scalar_value
        self.rows = rows
        self.get_value = get_value

    async def scalar(self, _statement):
        return self.scalar_value

    async def scalars(self, _statement):
        return iter(self.rows)

    async def get(self, _model, _identity):
        return self.get_value


def make_user(*, administrator: bool) -> User:
    return User(
        id=uuid.uuid4(),
        username="admin" if administrator else "alice",
        password_hash="unused",
        is_administrator=administrator,
        is_enabled=True,
        session_version=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_unassigned_knowledge_base_is_hidden() -> None:
    user = make_user(administrator=False)
    knowledge_base_id = uuid.uuid4()
    with pytest.raises(HTTPException) as error:
        await ensure_knowledge_base_access(FakeSession(False), user, knowledge_base_id)  # type: ignore[arg-type]
    assert getattr(error.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_administrator_has_implicit_knowledge_base_access() -> None:
    await ensure_knowledge_base_access(  # type: ignore[arg-type]
        FakeSession(False), make_user(administrator=True), uuid.uuid4()
    )


@pytest.mark.asyncio
async def test_session_version_change_invalidates_existing_cookie() -> None:
    user = make_user(administrator=False)
    token = create_session_token(user.id, user.session_version)
    user.session_version += 1
    with pytest.raises(HTTPException) as error:
        await get_current_user(FakeSession(get_value=user), token)  # type: ignore[arg-type]
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_admin_read_does_not_require_csrf_but_mutation_does() -> None:
    administrator = make_user(administrator=True)
    fake_session = FakeSession(rows=[administrator])

    async def override_session() -> AsyncIterator[FakeSession]:
        yield fake_session

    async def override_user() -> User:
        return administrator

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            read_response = await client.get("/api/v1/admin/users")
            write_response = await client.post(
                "/api/v1/admin/users",
                json={
                    "username": "bob",
                    "password": "a-secure-password",
                    "is_administrator": False,
                },
            )
        assert read_response.status_code == 200
        assert write_response.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cross_knowledge_base_request_returns_not_found() -> None:
    user = make_user(administrator=False)
    fake_session = FakeSession(scalar_value=False)

    async def override_session() -> AsyncIterator[FakeSession]:
        yield fake_session

    async def override_user() -> User:
        return user

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/knowledge-bases/{uuid.uuid4()}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Knowledge Base not found"}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_non_administrator_cannot_list_documents() -> None:
    user = make_user(administrator=False)

    async def override_session() -> AsyncIterator[FakeSession]:
        yield FakeSession()

    async def override_user() -> User:
        return user

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/admin/knowledge-bases/{uuid.uuid4()}/documents"
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_knowledge_base_with_documents_cannot_be_deleted() -> None:
    knowledge_base = KnowledgeBase(
        id=uuid.uuid4(),
        name="Operations",
        description=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session = FakeSession(scalar_value=1, get_value=knowledge_base)
    with pytest.raises(HTTPException) as error:
        await delete_knowledge_base(  # type: ignore[arg-type]
            knowledge_base.id,
            None,
            make_user(administrator=True),
            session,
        )
    assert error.value.status_code == 409
