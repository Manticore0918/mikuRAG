import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_liveness_does_not_depend_on_external_services() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_reports_dependency_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def not_ready():
        return False, {
            "database": {"status": "ok", "detail": None},
            "redis": {"status": "error", "detail": "redis unavailable"},
        }

    monkeypatch.setattr("app.main.readiness", not_ready)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["dependencies"]["redis"]["detail"] == "redis unavailable"
