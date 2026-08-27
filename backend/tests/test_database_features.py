from contextlib import AbstractAsyncContextManager
from types import TracebackType

import pytest

from app.database_features import reconcile_optional_database_features


class FakeResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar(self) -> object:
        return self.value


class FakeConnection:
    def __init__(self, *, extension_available: bool, fail_on: str | None = None) -> None:
        self.extension_available = extension_available
        self.fail_on = fail_on
        self.statements: list[str] = []

    async def execute(self, statement: object) -> FakeResult:
        rendered = str(statement)
        self.statements.append(rendered)
        if self.fail_on and self.fail_on in rendered:
            raise RuntimeError("fixture database failure")
        if "pg_available_extensions" in rendered:
            return FakeResult(self.extension_available)
        return FakeResult(None)


class FakeBegin(AbstractAsyncContextManager[FakeConnection]):
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return None


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def begin(self) -> AbstractAsyncContextManager[FakeConnection]:
        return FakeBegin(self.connection)


@pytest.mark.asyncio
async def test_reconcile_installs_extension_and_index_when_available() -> None:
    connection = FakeConnection(extension_available=True)

    status = await reconcile_optional_database_features(FakeEngine(connection))  # type: ignore[arg-type]

    assert status["status"] == "ready"
    assert any("CREATE EXTENSION" in statement for statement in connection.statements)
    assert any(
        "CREATE INDEX chunks_search_bm25" in statement
        for statement in connection.statements
    )


@pytest.mark.asyncio
async def test_reconcile_keeps_fts_when_extension_is_unavailable() -> None:
    connection = FakeConnection(extension_available=False)

    status = await reconcile_optional_database_features(FakeEngine(connection))  # type: ignore[arg-type]

    assert status["status"] == "unavailable"
    assert not any("CREATE EXTENSION" in statement for statement in connection.statements)


@pytest.mark.asyncio
async def test_reconcile_reports_optional_feature_failure_without_raising() -> None:
    connection = FakeConnection(extension_available=True, fail_on="CREATE EXTENSION")

    status = await reconcile_optional_database_features(FakeEngine(connection))  # type: ignore[arg-type]

    assert status["status"] == "error"
