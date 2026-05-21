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

    Each test runs in its own transaction which cleans all mutable tables
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
                # Clean slate: truncate all mutable tables at start of test
                await session.execute(
                    text(
                        "TRUNCATE coe_events, coe_runs, employees, "
                        "jira_raw, wiz_raw, crowdstrike_raw, vibranium_raw CASCADE"
                    )
                )
                await session.commit()
                yield session
                # Clean slate: truncate all mutable tables at end of test
                await session.execute(
                    text(
                        "TRUNCATE coe_events, coe_runs, employees, "
                        "jira_raw, wiz_raw, crowdstrike_raw, vibranium_raw CASCADE"
                    )
                )
                await session.commit()
            finally:
                # Rollback at end (implicitly done by engine.begin())
                await session.close()
    finally:
        await engine.dispose()
