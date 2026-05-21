"""Idempotent upsert helpers for coe_events and raw audit tables."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from coe.db.base import Base
from coe.db.models import CoeEvent, CrowdstrikeRaw, JiraRaw, Source, VibraniumRaw, WizRaw

_RAW_MODEL: dict[Source, type[Base]] = {
    Source.JIRA: JiraRaw,
    Source.WIZ: WizRaw,
    Source.CROWDSTRIKE: CrowdstrikeRaw,
    Source.VIBRANIUM: VibraniumRaw,
}


async def upsert_coe_events(session: AsyncSession, events: Iterable[CoeEvent]) -> int:
    """Upsert CoeEvent rows idempotently using SQLAlchemy dialect helpers.

    INSERT ... ON CONFLICT (source, source_id) DO UPDATE updates mutable fields
    (title, severity, status, owner_email, manager_email, missing_owner_in_hr,
    sla_due_at, priority, updated_at, last_seen_at, raw) while preserving
    immutable fields (id, source, source_id, opened_at, coe_review_status).

    Batches inserts in groups of 500 for memory efficiency.

    Args:
        session: AsyncSession for the database connection.
        events: Iterable of CoeEvent instances to upsert.

    Returns:
        Total number of rows processed (len(list(events))).

    Caller commits the session.
    """
    events_list = list(events)
    if not events_list:
        return 0

    total = len(events_list)
    batch_size = 500

    # Helper to convert CoeEvent instance to dict of column values
    def _event_to_row(evt: CoeEvent) -> dict[str, Any]:
        return {
            "source": evt.source.value if hasattr(evt.source, "value") else evt.source,
            "source_id": evt.source_id,
            "title": evt.title,
            "severity": evt.severity.value if hasattr(evt.severity, "value") else evt.severity,
            "status": evt.status,
            "owner_email": evt.owner_email,
            "manager_email": evt.manager_email,
            "missing_owner_in_hr": evt.missing_owner_in_hr,
            "sla_due_at": evt.sla_due_at,
            "priority": evt.priority,
            "opened_at": evt.opened_at,
            "updated_at": evt.updated_at,
            "raw": json.dumps(evt.raw) if isinstance(evt.raw, dict) else evt.raw,
        }

    # Batch upserts in groups of 500
    for i in range(0, len(events_list), batch_size):
        batch = events_list[i : i + batch_size]
        rows = [_event_to_row(evt) for evt in batch]

        # Use SQLAlchemy dialect insert with on_conflict_do_update
        stmt = insert(CoeEvent).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[CoeEvent.source, CoeEvent.source_id],
            set_={
                CoeEvent.title: stmt.excluded.title,
                CoeEvent.severity: stmt.excluded.severity,
                CoeEvent.status: stmt.excluded.status,
                CoeEvent.owner_email: stmt.excluded.owner_email,
                CoeEvent.manager_email: stmt.excluded.manager_email,
                CoeEvent.missing_owner_in_hr: stmt.excluded.missing_owner_in_hr,
                CoeEvent.sla_due_at: stmt.excluded.sla_due_at,
                CoeEvent.priority: stmt.excluded.priority,
                CoeEvent.updated_at: stmt.excluded.updated_at,
                CoeEvent.last_seen_at: func.statement_timestamp(),
                CoeEvent.raw: stmt.excluded.raw,
            },
        )
        await session.execute(stmt)

    return total


async def insert_raw_records(
    session: AsyncSession, source: Source, records: Iterable[Mapping[str, Any]]
) -> int:
    """Insert raw audit records idempotently.

    Uses INSERT ... ON CONFLICT (source_id, fetched_at) DO NOTHING to handle
    back-to-back runs where clock ticks might collide.

    Args:
        session: AsyncSession for the database connection.
        source: Source enum to pick the correct raw model from _RAW_MODEL.
        records: Iterable of Mapping[str, Any] with at minimum keys `source_id`
                 and `payload`. The `fetched_at` column has a server_default, so
                 callers don't supply it unless they want explicit control.

    Returns:
        Total number of records processed (len(list(records))).

    Caller commits the session.
    """
    records_list = list(records)
    if not records_list:
        return 0

    total = len(records_list)
    table_name = _RAW_MODEL[source].__tablename__

    # Batch inserts in chunks of 500
    batch_size = 500
    for i in range(0, len(records_list), batch_size):
        batch = records_list[i : i + batch_size]

        # Build VALUES clause with placeholders
        values_parts = []
        params = {}
        for idx, record in enumerate(batch):
            if "fetched_at" in record:
                # If fetched_at is explicitly provided, include it in the insert
                value_placeholders = f":p{idx}_source_id, :p{idx}_fetched_at, :p{idx}_payload"
                params[f"p{idx}_fetched_at"] = record["fetched_at"]
            else:
                # If fetched_at is not provided, let server_default handle it
                value_placeholders = f":p{idx}_source_id, DEFAULT, :p{idx}_payload"

            values_parts.append(f"({value_placeholders})")

            # Add parameters - need to convert payload dict to JSON string for asyncpg
            params[f"p{idx}_source_id"] = record["source_id"]
            params[f"p{idx}_payload"] = (
                json.dumps(record["payload"])
                if isinstance(record["payload"], dict)
                else record["payload"]
            )

        values_clause = ", ".join(values_parts)

        sql = f"""
            INSERT INTO {table_name} (source_id, fetched_at, payload)
            VALUES {values_clause}
            ON CONFLICT (source_id, fetched_at) DO NOTHING
        """

        await session.execute(text(sql), params)

    return total
