"""Shared fake asyncpg pool for store unit tests. No live database."""

from __future__ import annotations

from typing import Any

import pytest

from stores import db


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_result: str = "UPDATE 1"
        self.fetchrow_result: Any = None
        self.fetch_result: list[Any] = []
        self.fetchval_result: Any = None
        self.execute_side_effect: Any = None
        self.fetchrow_side_effect: Any = None

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        if self.execute_side_effect is not None:
            raise self.execute_side_effect
        return self.execute_result

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        if self.fetchrow_side_effect is not None:
            raise self.fetchrow_side_effect
        return self.fetchrow_result

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self.calls.append((sql, args))
        return self.fetch_result

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        return self.fetchval_result

    def transaction(self) -> "_FakeTransaction":
        return _FakeTransaction()


class _FakeTransaction:
    async def __aenter__(self) -> "_FakeTransaction":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeAcquire:
    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConnection:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConnection()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> FakePool:
    pool = FakePool()

    async def _get_pool() -> FakePool:
        return pool

    monkeypatch.setattr(db, "get_pool", _get_pool)
    return pool
