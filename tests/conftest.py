"""Top-level fixtures for pipeline integration tests.

Provides session-scoped database fixtures that use the local Postgres instance
(already running via podman compose) rather than spinning up a testcontainer.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from coe.config import get_settings


@pytest.fixture(scope="session")
def db_url() -> str:
    """Session-scoped: return the database URL pointing at local Postgres.

    The local Postgres is already running via podman compose as coe-postgres
    on localhost:5432.
    """
    return get_settings().database_url


@pytest.fixture
async def session_factory(
    db_url: str,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Per-test: create a session factory and truncate tables before/after.

    Yields a fresh async_sessionmaker for each test. Before the test runs,
    truncates all mutable tables to provide test isolation. After the test,
    truncates again to clean up.

    This fixture is per-test (not session-scoped) so each test starts with
    a clean database.

    Args:
        db_url: Session-scoped database URL from db_url fixture.

    Yields:
        async_sessionmaker[AsyncSession] for creating sessions within the test.
    """
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Truncate before test
    async with factory() as session:
        await session.execute(
            text(
                "TRUNCATE coe_events, coe_runs, employees, "
                "jira_raw, wiz_raw, crowdstrike_raw, vibranium_raw CASCADE"
            )
        )
        await session.commit()

    yield factory

    # Truncate after test
    async with factory() as session:
        await session.execute(
            text(
                "TRUNCATE coe_events, coe_runs, employees, "
                "jira_raw, wiz_raw, crowdstrike_raw, vibranium_raw CASCADE"
            )
        )
        await session.commit()

    # Cleanup
    await engine.dispose()


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Per-test: convenience fixture for tests that need a single session.

    Yields a fresh AsyncSession for use within the test.

    Args:
        session_factory: Per-test session factory from session_factory fixture.

    Yields:
        A fresh AsyncSession for the duration of the test.
    """
    async with session_factory() as s:
        yield s
