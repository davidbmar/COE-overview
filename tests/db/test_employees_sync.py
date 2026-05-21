"""Integration tests for coe.db.employees upsert helper.

Tests exercise INSERT ... ON CONFLICT behavior and idempotency.
Requires: Postgres running and schema migrated (alembic upgrade head).
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coe.db.employees import upsert_employees
from coe.db.models import Employee as EmployeeORM
from coe.ingest.hr import HrEmployee

pytestmark = pytest.mark.integration


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
    """Upsert same 3 records again → still 3 rows, last_synced_at advances."""
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

    # Capture last_synced_at from each row after first upsert
    ts_after_first = {r.email: r.last_synced_at for r in rows_after_first}

    # Sleep briefly to guarantee distinct Postgres transaction timestamp
    await asyncio.sleep(0.01)

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

    # Verify last_synced_at advanced for every row
    ts_after_second = {r.email: r.last_synced_at for r in rows_after_second}
    for email in ts_after_first:
        assert ts_after_second[email] > ts_after_first[email], (
            f"last_synced_at did not advance for {email}"
        )


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
