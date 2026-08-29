"""Shared fixtures for integration tests (run with `-m integration`)."""

import pytest_asyncio


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def settings():
    from app.config import get_settings

    return get_settings()
