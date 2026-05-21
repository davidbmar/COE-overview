"""Shared fixtures for ingest tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def mock_asyncio_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-use fixture: monkeypatch asyncio.sleep to a no-op.

    This prevents retry tests from sleeping through full exponential backoff
    ladders, reducing test suite runtime from ~150s to < 5s. Tests that need
    to assert sleep durations can explicitly opt out or capture durations
    from the mock.
    """
    async_mock = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", async_mock)
