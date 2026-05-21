"""Integration tests for coe.db.employees upsert helper.

Tests exercise INSERT ... ON CONFLICT behavior and idempotency.
Requires: Postgres running and schema migrated (alembic upgrade head).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from coe.config import get_settings
from coe.db.employees import upsert_employees
from coe.db.models import Employee as EmployeeORM
from coe.ingest.hr import HrEmployee

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Fixture: create an AsyncSession with transaction per test.

    Each test runs in its own transaction which is rolled back after,
    providing test isolation.
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
                # Commit this delete
                await session.commit()
                yield session
            finally:
                # Rollback at end (implicitly done by engine.begin())
                await session.close()
    finally:
        await engine.dispose()


async def test_upsert_empty_table_inserts_three_records(db_session: AsyncSession) -> None:
    """Upsert 3 records into empty table → 3 rows inserted."""
    records = [
        HrEmployee(
            email="alice@x.com", manager_email="mgr1@x.com", org_path="/eng", is_active=True
        ),
        HrEmployee(email="bob@x.com", manager_email="mgr2@x.com", org_path="/sec", is_active=True),
        HrEmployee(email="charlie@x.com", manager_email=None, org_path="/ops", is_active=True),
    ]

    count = await upsert_employees(db_session, records)
    await db_session.commit()

    assert count == 3
    rows = (await db_session.execute(select(EmployeeORM))).scalars().all()
    assert len(rows) == 3
    assert {r.email for r in rows} == {"alice@x.com", "bob@x.com", "charlie@x.com"}
    assert {r.manager_email for r in rows} == {"mgr1@x.com", "mgr2@x.com", None}


async def test_upsert_same_records_idempotent(db_session: AsyncSession) -> None:
    """Upsert same 3 records again → still 3 rows (idempotent)."""
    records = [
        HrEmployee(
            email="alice@x.com", manager_email="mgr1@x.com", org_path="/eng", is_active=True
        ),
        HrEmployee(email="bob@x.com", manager_email="mgr2@x.com", org_path="/sec", is_active=True),
        HrEmployee(email="charlie@x.com", manager_email=None, org_path="/ops", is_active=True),
    ]

    # First upsert
    count1 = await upsert_employees(db_session, records)
    await db_session.commit()
    db_session.expire_all()
    rows_after_first = (await db_session.execute(select(EmployeeORM))).scalars().all()
    assert len(rows_after_first) == 3

    # Second upsert (same records)
    count2 = await upsert_employees(db_session, records)
    await db_session.commit()
    db_session.expire_all()

    assert count1 == 3
    assert count2 == 3
    rows_after_second = (await db_session.execute(select(EmployeeORM))).scalars().all()
    assert len(rows_after_second) == 3
    # Verify data is unchanged
    assert {r.email for r in rows_after_second} == {"alice@x.com", "bob@x.com", "charlie@x.com"}
    assert {r.manager_email for r in rows_after_second} == {"mgr1@x.com", "mgr2@x.com", None}


async def test_upsert_modify_one_manager_email(db_session: AsyncSession) -> None:
    """Upsert 3 records, modify one manager_email, re-upsert → only that row updated."""
    records_v1 = [
        HrEmployee(
            email="alice@x.com", manager_email="mgr1@x.com", org_path="/eng", is_active=True
        ),
        HrEmployee(email="bob@x.com", manager_email="mgr2@x.com", org_path="/sec", is_active=True),
        HrEmployee(email="charlie@x.com", manager_email=None, org_path="/ops", is_active=True),
    ]

    await upsert_employees(db_session, records_v1)
    await db_session.commit()
    db_session.expire_all()
    rows_before = {
        r.email: r for r in (await db_session.execute(select(EmployeeORM))).scalars().all()
    }
    assert rows_before["bob@x.com"].manager_email == "mgr2@x.com"

    # Re-upsert with bob's manager_email changed
    records_v2 = [
        HrEmployee(
            email="alice@x.com", manager_email="mgr1@x.com", org_path="/eng", is_active=True
        ),
        HrEmployee(
            email="bob@x.com", manager_email="NEW_MGR@x.com", org_path="/sec", is_active=True
        ),
        HrEmployee(email="charlie@x.com", manager_email=None, org_path="/ops", is_active=True),
    ]

    count = await upsert_employees(db_session, records_v2)
    await db_session.commit()
    db_session.expire_all()

    assert count == 3
    rows_after = {
        r.email: r for r in (await db_session.execute(select(EmployeeORM))).scalars().all()
    }
    assert len(rows_after) == 3

    # Only bob's manager_email changed
    assert rows_after["bob@x.com"].manager_email == "NEW_MGR@x.com"
    assert rows_after["alice@x.com"].manager_email == "mgr1@x.com"
    assert rows_after["charlie@x.com"].manager_email is None
