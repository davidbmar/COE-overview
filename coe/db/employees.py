"""Helper for upserting employee records into the employees table.

Uses Postgres INSERT ... ON CONFLICT (email) DO UPDATE for idempotent
synchronization of the local employee directory.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession

from coe.db.models import Employee as EmployeeORM
from coe.ingest.hr import HrEmployee


async def upsert_employees(
    session: AsyncSession,
    records: Iterable[HrEmployee],
) -> int:
    """Upsert employee records into the employees table.

    Uses Postgres INSERT ... ON CONFLICT (email) DO UPDATE. On conflict,
    updates manager_email, org_path, and bumps last_synced_at to now().

    Args:
        session: AsyncSession for the database operation. Does NOT commit;
                 caller manages transaction boundary.
        records: Iterable of HrEmployee records to upsert.

    Returns:
        Count of rows processed (i.e., len(list(records))).
    """
    # Convert to list to allow multiple iterations
    records_list = list(records)
    if not records_list:
        return 0

    # Build values dict for INSERT (email, manager_email, org_path)
    # On INSERT, the database will set last_synced_at via server_default
    values = [
        {
            "email": r.email,
            "manager_email": r.manager_email,
            "org_path": r.org_path,
        }
        for r in records_list
    ]

    # Build Postgres-specific insert statement
    stmt = postgres_insert(EmployeeORM).values(values)

    # ON CONFLICT: update manager_email, org_path, last_synced_at
    # Use stmt.excluded to reference the VALUES from the INSERT
    # Use statement_timestamp() to get the current time at statement execution (not tx start)
    stmt = stmt.on_conflict_do_update(
        index_elements=["email"],
        set_={
            "manager_email": stmt.excluded.manager_email,
            "org_path": stmt.excluded.org_path,
            "last_synced_at": func.statement_timestamp(),
        },
    )

    await session.execute(stmt)
    return len(records_list)
