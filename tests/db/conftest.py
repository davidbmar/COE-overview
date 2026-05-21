"""Shared fixtures for DB-backed integration tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from coe.config import get_settings


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Fixture: create an AsyncSession with transaction per test.

    Each test runs in its own transaction which deletes all employees
    before and after the test, providing test isolation.
    """
    engine = create_async_engine(get_settings().database_url)
    try:
        # Use engine.begin() to get a transaction connection
        async with engine.begin() as conn:
            # Create a session bound to that connection
            session = AsyncSession(
                bind=conn,
                expire_on_commit=False,
            )
            try:
                # Clean slate: delete all employees at start of test
                await session.execute(text("DELETE FROM employees"))
                await session.commit()
                yield session
                # Clean slate: delete all employees at end of test
                await session.execute(text("DELETE FROM employees"))
                await session.commit()
            finally:
                # Rollback at end (implicitly done by engine.begin())
                await session.close()
    finally:
        await engine.dispose()
