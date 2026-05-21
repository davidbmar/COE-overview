"""Integration tests for coe.owner_resolver.load_resolver (DB-backed).

Tests the load_resolver function against a real Postgres database.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from coe.config import get_settings
from coe.db.models import Employee as EmployeeORM
from coe.owner_resolver import load_resolver

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Fixture: create an AsyncSession with transaction rollback."""
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.begin() as conn:
            session = AsyncSession(bind=conn, expire_on_commit=False)
            try:
                # Clean slate: delete all employees
                await session.execute(text("DELETE FROM employees"))
                await session.commit()
                yield session
            finally:
                await session.close()
    finally:
        await engine.dispose()


async def test_load_resolver_seeds_from_employees_table(db_session: AsyncSession) -> None:
    """load_resolver loads all employees from the table and resolves correctly."""
    # Seed employees table manually
    emp1 = EmployeeORM(
        email="alice@x.com",
        manager_email="mgr1@x.com",
        org_path="/eng",
    )
    emp2 = EmployeeORM(
        email="bob@x.com",
        manager_email=None,
        org_path="/ops",
    )

    db_session.add(emp1)
    db_session.add(emp2)
    await db_session.commit()

    # Load resolver
    resolver = await load_resolver(db_session)

    # Verify resolution works for both
    result1 = resolver.resolve("Alice@X.com")
    assert result1.owner_email == "Alice@X.com"
    assert result1.manager_email == "mgr1@x.com"
    assert result1.missing_owner_in_hr is False

    result2 = resolver.resolve("bob@x.com")
    assert result2.owner_email == "bob@x.com"
    assert result2.manager_email is None
    assert result2.missing_owner_in_hr is False

    # Verify unknown owner still returns missing flag
    result_unknown = resolver.resolve("unknown@x.com")
    assert result_unknown.owner_email == "unknown@x.com"
    assert result_unknown.manager_email is None
    assert result_unknown.missing_owner_in_hr is True
