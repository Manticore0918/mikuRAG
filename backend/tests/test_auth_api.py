import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_session
from app.main import app
from app.models import User
from app.rate_limit import login_rate_limiter
from app.security import CSRF_COOKIE, SESSION_COOKIE, hash_password


class UserSession:
    def __init__(self, user: User):
        self.user = user

    async def scalar(self, _statement):
        return self.user

    async def get(self, _model, identity):
        return self.user if identity == self.user.id else None


@pytest.mark.asyncio
async def test_login_sets_checked_session_and_logout_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(
        id=uuid.uuid4(),
        username="alice",
        password_hash=hash_password("a-secure-password"),
        is_administrator=False,
        is_enabled=True,
        session_version=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    async def override_session() -> AsyncIterator[UserSession]:
        yield UserSession(user)

    async def no_op(_key: str) -> None:
        return None

    monkeypatch.setattr(login_rate_limiter, "ensure_allowed", no_op)
    monkeypatch.setattr(login_rate_limiter, "clear", no_op)
    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            csrf_response = await client.get("/api/v1/auth/csrf")
            login_response = await client.post(
                "/api/v1/auth/login",
                headers={"X-CSRF-Token": csrf_response.json()["csrf_token"]},
                json={"username": "Alice", "password": "a-secure-password"},
            )
            assert login_response.status_code == 200
            assert client.cookies.get(SESSION_COOKIE)

            current_response = await client.get("/api/v1/auth/me")
            assert current_response.status_code == 200
            assert current_response.json()["username"] == "alice"

            logout_response = await client.post(
                "/api/v1/auth/logout",
                headers={"X-CSRF-Token": client.cookies.get(CSRF_COOKIE, "")},
            )
            assert logout_response.status_code == 204
            assert client.cookies.get(SESSION_COOKIE) is None
    finally:
        app.dependency_overrides.clear()

