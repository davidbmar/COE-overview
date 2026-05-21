"""Tests for the section query layer (coe/doc/sections.py).

AC4.1: Data layer for sections.
AC4.3: Missing-owner bucketing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coe.db.models import CoeEvent, CoeSeverity, Source
from coe.doc.sections import DocSections, build_sections


@pytest.mark.integration
async def test_ac4_1_bucket_each_section(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """AC4.1: Each bucket (new, changed, missing_owner, missing_sla, recently_resolved)
    receives exactly one event when seeded with one event per bucket.
    """
    async with session_factory() as session:
        now = datetime.now(UTC)
        since = now - timedelta(days=7)

        # Bucket 1: missing_owner
        missing_owner_event = CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-001",
            title="Event with no owner",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email=None,  # NULL owner
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),  # Has SLA
            priority=None,
            opened_at=since - timedelta(days=1),  # Old event
            updated_at=since - timedelta(days=1),
            coe_review_status="open",  # Not resolved
            raw={},
        )

        # Bucket 2: missing_sla
        missing_sla_event = CoeEvent(
            source=Source.WIZ,
            source_id="WIZ-002",
            title="Event with no SLA",
            severity=CoeSeverity.CRITICAL,
            status="open",
            owner_email="owner@example.com",  # Has owner
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=None,  # NULL SLA
            priority=None,
            opened_at=since - timedelta(days=1),  # Old event
            updated_at=since - timedelta(days=1),
            coe_review_status="open",  # Not resolved
            raw={},
        )

        # Bucket 3: new
        new_event = CoeEvent(
            source=Source.CROWDSTRIKE,
            source_id="CS-003",
            title="Newly opened event",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="owner@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since + timedelta(hours=1),  # Recently opened (> since)
            updated_at=since + timedelta(hours=1),
            coe_review_status="open",
            raw={},
        )

        # Bucket 4: changed
        changed_event = CoeEvent(
            source=Source.VIBRANIUM,
            source_id="VIBE-004",
            title="Recently changed event",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="owner@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since - timedelta(days=5),  # Old opened_at (<= since)
            updated_at=since + timedelta(hours=1),  # Recently updated (> since)
            coe_review_status="open",
            raw={},
        )

        # Bucket 5: recently_resolved
        resolved_event = CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-005",
            title="Recently resolved event",
            severity=CoeSeverity.HIGH,
            status="closed",
            owner_email="owner@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since - timedelta(days=30),
            updated_at=now - timedelta(days=3),  # Updated within 7-day window
            coe_review_status="resolved",
            raw={},
        )

        session.add_all([
            missing_owner_event,
            missing_sla_event,
            new_event,
            changed_event,
            resolved_event,
        ])
        await session.commit()

        # Build sections with since = 7 days ago
        sections: DocSections = await build_sections(session, since)

        # Verify each event lands in exactly one bucket
        assert len(sections.missing_owner) == 1
        assert len(sections.missing_sla) == 1
        assert len(sections.new) == 1
        assert len(sections.changed) == 1
        assert len(sections.recently_resolved) == 1

        # Verify the correct events are in each bucket
        assert sections.missing_owner[0].source_id == "JIRA-001"
        assert sections.missing_sla[0].source_id == "WIZ-002"
        assert sections.new[0].source_id == "CS-003"
        assert sections.changed[0].source_id == "VIBE-004"
        assert sections.recently_resolved[0].source_id == "JIRA-005"

        # Total events = sum of all sections
        total = (
            len(sections.missing_owner)
            + len(sections.missing_sla)
            + len(sections.new)
            + len(sections.changed)
            + len(sections.recently_resolved)
        )
        assert total == 5


@pytest.mark.integration
async def test_ac4_3_missing_owner_precedence(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """AC4.3: An event with owner_email=None and opened_at > since lands in
    missing_owner, NOT in new (precedence rule).
    """
    async with session_factory() as session:
        now = datetime.now(UTC)
        since = now - timedelta(days=7)

        # Event with NULL owner_email but opened_at > since
        event = CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-001",
            title="Missing owner, newly opened",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email=None,  # NULL owner → missing_owner bucket
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since + timedelta(hours=1),  # opened_at > since (would be "new")
            updated_at=since + timedelta(hours=1),
            coe_review_status="open",  # Not resolved
            raw={},
        )

        session.add(event)
        await session.commit()

        sections: DocSections = await build_sections(session, since)

        # Event should be in missing_owner, NOT in new
        assert len(sections.missing_owner) == 1
        assert len(sections.new) == 0
        assert sections.missing_owner[0].source_id == "JIRA-001"


@pytest.mark.integration
async def test_ac4_3_missing_owner_and_missing_sla_precedence(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Precedence: event with owner_email=None AND sla_due_at=None lands in
    missing_owner (the FIRST bucket wins).
    """
    async with session_factory() as session:
        now = datetime.now(UTC)
        since = now - timedelta(days=7)

        # Event with both NULL owner and NULL SLA
        event = CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-001",
            title="Missing both owner and SLA",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email=None,  # Missing owner
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=None,  # Missing SLA
            priority=None,
            opened_at=since - timedelta(days=1),
            updated_at=since - timedelta(days=1),
            coe_review_status="open",
            raw={},
        )

        session.add(event)
        await session.commit()

        sections: DocSections = await build_sections(session, since)

        # Event should be in missing_owner (first bucket), NOT in missing_sla
        assert len(sections.missing_owner) == 1
        assert len(sections.missing_sla) == 0
        assert sections.missing_owner[0].source_id == "JIRA-001"


@pytest.mark.integration
async def test_resolved_window_7_day_exclusion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A resolved event updated 8 days ago is excluded from recently_resolved
    with a 7-day window.
    """
    async with session_factory() as session:
        now = datetime.now(UTC)
        since = now - timedelta(days=30)

        # Event resolved 8 days ago (outside 7-day window)
        old_resolved_event = CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-001",
            title="Resolved 8 days ago",
            severity=CoeSeverity.HIGH,
            status="closed",
            owner_email="owner@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since,
            updated_at=now - timedelta(days=8),  # 8 days ago (outside window)
            coe_review_status="resolved",
            raw={},
        )

        # Event resolved 3 days ago (inside 7-day window)
        recent_resolved_event = CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-002",
            title="Resolved 3 days ago",
            severity=CoeSeverity.HIGH,
            status="closed",
            owner_email="owner@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since,
            updated_at=now - timedelta(days=3),  # 3 days ago (inside window)
            coe_review_status="resolved",
            raw={},
        )

        session.add_all([old_resolved_event, recent_resolved_event])
        await session.commit()

        sections: DocSections = await build_sections(session, since)

        # Only the recent one should be in recently_resolved
        assert len(sections.recently_resolved) == 1
        assert sections.recently_resolved[0].source_id == "JIRA-002"


@pytest.mark.integration
async def test_sort_order_by_severity_and_updated_at(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Within a section, CRITICAL appears before HIGH appears before UNKNOWN;
    within same severity, newer updated_at first.
    """
    async with session_factory() as session:
        now = datetime.now(UTC)
        since = now - timedelta(days=7)

        # Create events in mixed order with various severities and timestamps
        events = [
            CoeEvent(
                source=Source.JIRA,
                source_id="JIRA-001",
                title="HIGH, updated 2 days ago",
                severity=CoeSeverity.HIGH,
                status="open",
                owner_email="owner@example.com",
                manager_email=None,
                missing_owner_in_hr=False,
                sla_due_at=now + timedelta(days=1),
                priority=None,
                opened_at=since + timedelta(hours=1),
                updated_at=now - timedelta(days=2),
                coe_review_status="open",
                raw={},
            ),
            CoeEvent(
                source=Source.JIRA,
                source_id="JIRA-002",
                title="UNKNOWN, updated 1 day ago",
                severity=CoeSeverity.UNKNOWN,
                status="open",
                owner_email="owner@example.com",
                manager_email=None,
                missing_owner_in_hr=False,
                sla_due_at=now + timedelta(days=1),
                priority=None,
                opened_at=since + timedelta(hours=2),
                updated_at=now - timedelta(days=1),
                coe_review_status="open",
                raw={},
            ),
            CoeEvent(
                source=Source.JIRA,
                source_id="JIRA-003",
                title="CRITICAL, updated 3 days ago",
                severity=CoeSeverity.CRITICAL,
                status="open",
                owner_email="owner@example.com",
                manager_email=None,
                missing_owner_in_hr=False,
                sla_due_at=now + timedelta(days=1),
                priority=None,
                opened_at=since + timedelta(hours=3),
                updated_at=now - timedelta(days=3),
                coe_review_status="open",
                raw={},
            ),
            CoeEvent(
                source=Source.JIRA,
                source_id="JIRA-004",
                title="HIGH, updated 1 day ago (newer)",
                severity=CoeSeverity.HIGH,
                status="open",
                owner_email="owner@example.com",
                manager_email=None,
                missing_owner_in_hr=False,
                sla_due_at=now + timedelta(days=1),
                priority=None,
                opened_at=since + timedelta(hours=4),
                updated_at=now - timedelta(days=1),
                coe_review_status="open",
                raw={},
            ),
            CoeEvent(
                source=Source.JIRA,
                source_id="JIRA-005",
                title="CRITICAL, updated 1 day ago",
                severity=CoeSeverity.CRITICAL,
                status="open",
                owner_email="owner@example.com",
                manager_email=None,
                missing_owner_in_hr=False,
                sla_due_at=now + timedelta(days=1),
                priority=None,
                opened_at=since + timedelta(hours=5),
                updated_at=now - timedelta(days=1),
                coe_review_status="open",
                raw={},
            ),
        ]

        session.add_all(events)
        await session.commit()

        sections: DocSections = await build_sections(session, since)

        # All events should be in the "new" bucket
        assert len(sections.new) == 5

        # Verify sort order:
        # 1. CRITICAL, updated 1 day ago (JIRA-005)
        # 2. CRITICAL, updated 3 days ago (JIRA-003)
        # 3. HIGH, updated 1 day ago (JIRA-004)
        # 4. HIGH, updated 2 days ago (JIRA-001)
        # 5. UNKNOWN, updated 1 day ago (JIRA-002)

        assert sections.new[0].source_id == "JIRA-005"  # CRITICAL, newest
        assert sections.new[1].source_id == "JIRA-003"  # CRITICAL, older
        assert sections.new[2].source_id == "JIRA-004"  # HIGH, newer
        assert sections.new[3].source_id == "JIRA-001"  # HIGH, older
        assert sections.new[4].source_id == "JIRA-002"  # UNKNOWN
