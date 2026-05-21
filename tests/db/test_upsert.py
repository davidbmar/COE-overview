"""Tests for idempotent upsert helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coe.db.models import CoeEvent, CoeSeverity, JiraRaw, Source
from coe.db.upsert import insert_raw_records, upsert_coe_events


@pytest.mark.integration
async def test_upsert_coe_events_idempotent(db_session: AsyncSession) -> None:
    """AC3.2: Upserting same 3 events twice results in 3 rows total (not 6).

    Second upsert updates last_seen_at to a newer value.
    """
    # Create 3 events
    now = datetime.now(UTC)
    events = [
        CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-1",
            title="Event 1",
            severity=CoeSeverity.CRITICAL,
            status="open",
            owner_email="owner1@example.com",
            manager_email="mgr1@example.com",
            missing_owner_in_hr=False,
            sla_due_at=now,
            priority="P1",
            opened_at=now,
            updated_at=now,
            raw={"key": "JIRA-1"},
        ),
        CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-2",
            title="Event 2",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="owner2@example.com",
            manager_email="mgr2@example.com",
            missing_owner_in_hr=False,
            sla_due_at=now,
            priority="P2",
            opened_at=now,
            updated_at=now,
            raw={"key": "JIRA-2"},
        ),
        CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-3",
            title="Event 3",
            severity=CoeSeverity.UNKNOWN,
            status="closed",
            owner_email="owner3@example.com",
            manager_email="mgr3@example.com",
            missing_owner_in_hr=False,
            sla_due_at=None,
            priority="P3",
            opened_at=now,
            updated_at=now,
            raw={"key": "JIRA-3"},
        ),
    ]

    # First upsert
    result1 = await upsert_coe_events(db_session, events)
    await db_session.commit()
    assert result1 == 3

    # Verify 3 rows in DB and capture last_seen_at
    rows = await db_session.execute(select(CoeEvent))
    all_rows = list(rows.scalars())
    assert len(all_rows) == 3
    first_last_seen = {row.source_id: row.last_seen_at for row in all_rows}

    # Wait a tiny bit to ensure distinct statement_timestamp
    await asyncio.sleep(0.01)

    # Second upsert with same events
    result2 = await upsert_coe_events(db_session, events)
    await db_session.commit()
    assert result2 == 3

    # Need to expire the session to get fresh data from the database
    db_session.expunge_all()

    # Verify still 3 rows (not 6)
    rows = await db_session.execute(select(CoeEvent).order_by(CoeEvent.source_id))
    all_rows = list(rows.scalars())
    assert len(all_rows) == 3

    # Verify last_seen_at was updated on second run
    for row in all_rows:
        assert row.last_seen_at > first_last_seen[row.source_id]


@pytest.mark.integration
async def test_upsert_coe_events_updates_title_only(db_session: AsyncSession) -> None:
    """AC3.2: Modifying title on one event and re-upserting updates only that row's title.

    Other rows and opened_at are preserved.
    """
    now = datetime.now(UTC)
    events = [
        CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-1",
            title="Original Title",
            severity=CoeSeverity.CRITICAL,
            status="open",
            owner_email="owner@example.com",
            manager_email="mgr@example.com",
            missing_owner_in_hr=False,
            sla_due_at=now,
            priority="P1",
            opened_at=now,
            updated_at=now,
            raw={"key": "JIRA-1"},
        ),
        CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-2",
            title="Event 2",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="owner2@example.com",
            manager_email="mgr2@example.com",
            missing_owner_in_hr=False,
            sla_due_at=None,
            priority="P2",
            opened_at=now,
            updated_at=now,
            raw={"key": "JIRA-2"},
        ),
    ]

    # First upsert
    await upsert_coe_events(db_session, events)
    await db_session.commit()

    # Capture opened_at and title from first run
    rows = await db_session.execute(select(CoeEvent).order_by(CoeEvent.source_id))
    first_rows = list(rows.scalars())
    assert first_rows[0].title == "Original Title"
    first_opened_at_1 = first_rows[0].opened_at
    first_opened_at_2 = first_rows[1].opened_at

    # Wait to ensure distinct timestamp
    await asyncio.sleep(0.01)

    # Modify title on first event and re-upsert
    modified_events = [
        CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-1",
            title="Updated Title",
            severity=CoeSeverity.CRITICAL,
            status="open",
            owner_email="owner@example.com",
            manager_email="mgr@example.com",
            missing_owner_in_hr=False,
            sla_due_at=now,
            priority="P1",
            opened_at=now,
            updated_at=now,
            raw={"key": "JIRA-1"},
        ),
        CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-2",
            title="Event 2",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="owner2@example.com",
            manager_email="mgr2@example.com",
            missing_owner_in_hr=False,
            sla_due_at=None,
            priority="P2",
            opened_at=now,
            updated_at=now,
            raw={"key": "JIRA-2"},
        ),
    ]

    await upsert_coe_events(db_session, modified_events)
    await db_session.commit()

    # Need to expire the session to get fresh data from the database
    db_session.expunge_all()

    # Verify title updated on first row only
    rows = await db_session.execute(select(CoeEvent).order_by(CoeEvent.source_id))
    updated_rows = list(rows.scalars())
    assert updated_rows[0].title == "Updated Title"
    assert updated_rows[1].title == "Event 2"

    # Verify opened_at was NOT changed (preserved)
    assert updated_rows[0].opened_at == first_opened_at_1
    assert updated_rows[1].opened_at == first_opened_at_2


@pytest.mark.integration
async def test_insert_raw_records_to_jira_raw(db_session: AsyncSession) -> None:
    """Test inserting raw records to jira_raw table."""
    records = [
        {"source_id": "ABC-1", "payload": {"key": "ABC-1", "summary": "Test issue"}},
    ]

    result = await insert_raw_records(db_session, Source.JIRA, records)
    await db_session.commit()
    assert result == 1

    # Verify row in jira_raw
    rows = await db_session.execute(select(JiraRaw))
    all_rows = list(rows.scalars())
    assert len(all_rows) == 1
    assert all_rows[0].source_id == "ABC-1"
    assert all_rows[0].payload == {"key": "ABC-1", "summary": "Test issue"}


@pytest.mark.integration
async def test_insert_raw_records_on_conflict_do_nothing(db_session: AsyncSession) -> None:
    """Test ON CONFLICT DO NOTHING: same source_id inserted twice with equal fetched_at."""
    # Insert the same record with explicit fetched_at
    fetched_ts = datetime.now(UTC)
    records = [
        {"source_id": "XYZ-1", "payload": {"id": "XYZ-1"}, "fetched_at": fetched_ts},
    ]

    # First insert
    result1 = await insert_raw_records(db_session, Source.JIRA, records)
    await db_session.commit()
    assert result1 == 1

    # Second insert with same source_id and fetched_at should not error
    result2 = await insert_raw_records(db_session, Source.JIRA, records)
    await db_session.commit()
    assert result2 == 1

    # Verify only 1 row in jira_raw
    rows = await db_session.execute(select(JiraRaw))
    all_rows = list(rows.scalars())
    assert len(all_rows) == 1
