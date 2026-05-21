"""Section query layer for the weekly prep doc.

Pulls events from the database and partitions them into five sections:
- new: opened after `since`
- changed: opened before/at `since`, updated after `since`
- missing_owner: no owner email and not resolved
- missing_sla: no SLA and not resolved (and has owner)
- recently_resolved: resolved within the window

Each section is sorted by severity (CRITICAL, HIGH, UNKNOWN) then updated_at DESC.

AC4.1: Data layer for sections.
AC4.3: Missing-owner bucketing with correct precedence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from coe.db.models import CoeEvent, CoeSeverity


@dataclass(frozen=True)
class DocSections:
    """Five sections of events for the weekly prep doc."""

    new: list[CoeEvent]
    changed: list[CoeEvent]
    missing_owner: list[CoeEvent]
    missing_sla: list[CoeEvent]
    recently_resolved: list[CoeEvent]


async def build_sections(
    session: AsyncSession,
    since: datetime,
    *,
    resolved_window: timedelta = timedelta(days=7),
) -> DocSections:
    """Build the five sections of events for the weekly prep doc.

    Events are bucketed in order of precedence (each event in exactly one):
    1. missing_owner: coe_review_status != 'resolved' AND owner_email IS NULL
    2. missing_sla: not above AND coe_review_status != 'resolved' AND sla_due_at IS NULL
    3. new: not above AND opened_at > since
    4. changed: not above AND opened_at <= since AND updated_at > since
    5. recently_resolved: coe_review_status = 'resolved' AND updated_at >= now() - resolved_window

    Within each section, sort by severity (CRITICAL, HIGH, UNKNOWN) then updated_at DESC.

    Args:
        session: AsyncSession for database queries.
        since: Timestamp to partition new/changed from old events.
        resolved_window: Timedelta to include recently-resolved events (default 7 days).

    Returns:
        DocSections with the five bucketed and sorted event lists.
    """
    # Pull all candidate rows in a single query: unresolved events OR recently resolved.
    result = await session.execute(
        select(CoeEvent).where(
            or_(
                CoeEvent.coe_review_status != "resolved",
                and_(
                    CoeEvent.coe_review_status == "resolved",
                    CoeEvent.updated_at >= func.now() - resolved_window,
                ),
            )
        )
    )
    all_events = result.scalars().all()

    # Compute the resolved threshold in Python for consistent comparisons
    # Use timezone-aware datetime in UTC
    now = datetime.now(UTC) if since.tzinfo is None else datetime.now(since.tzinfo)
    resolved_cutoff = now - resolved_window

    # Python-side bucketing for clarity
    missing_owner_list: list[CoeEvent] = []
    missing_sla_list: list[CoeEvent] = []
    new_list: list[CoeEvent] = []
    changed_list: list[CoeEvent] = []
    recently_resolved_list: list[CoeEvent] = []

    for event in all_events:
        # Bucket 1: missing_owner
        if event.coe_review_status != "resolved" and event.owner_email is None:
            missing_owner_list.append(event)
        # Bucket 2: missing_sla
        elif event.coe_review_status != "resolved" and event.sla_due_at is None:
            missing_sla_list.append(event)
        # Bucket 3: new (unresolved events only)
        elif event.coe_review_status != "resolved" and event.opened_at > since:
            new_list.append(event)
        # Bucket 4: changed (unresolved events only)
        elif (
            event.coe_review_status != "resolved"
            and event.opened_at <= since
            and event.updated_at > since
        ):
            changed_list.append(event)
        # Bucket 5: recently_resolved
        elif event.coe_review_status == "resolved" and event.updated_at >= resolved_cutoff:
            recently_resolved_list.append(event)

    # Sort each bucket: severity (CRITICAL, HIGH, UNKNOWN) then updated_at DESC
    def sort_key(e: CoeEvent) -> tuple[int, float]:
        """Sort by severity (lower is better) then updated_at DESC."""
        severity_order = {
            CoeSeverity.CRITICAL: 0,
            CoeSeverity.HIGH: 1,
            CoeSeverity.UNKNOWN: 2,
        }
        return (severity_order.get(e.severity, 999), -e.updated_at.timestamp())

    missing_owner_list.sort(key=sort_key)
    missing_sla_list.sort(key=sort_key)
    new_list.sort(key=sort_key)
    changed_list.sort(key=sort_key)
    recently_resolved_list.sort(key=sort_key)

    return DocSections(
        new=new_list,
        changed=changed_list,
        missing_owner=missing_owner_list,
        missing_sla=missing_sla_list,
        recently_resolved=recently_resolved_list,
    )
