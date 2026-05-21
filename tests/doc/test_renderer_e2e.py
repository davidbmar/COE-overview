"""End-to-end integration tests for coe/doc/renderer.py

Combines real database writes via session_factory fixture with mocked Google
Docs API responses via monkeypatch.

Verifies:
- AC4.1: Full section coverage with all 5 events
- AC4.2: Source links on each event
- AC4.3: Missing owner precedence in document
- AC4.4: API failure handling with no Postgres mutation
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coe.config import get_settings
from coe.db.models import CoeEvent, CoeRun, CoeSeverity, Source
from coe.doc.google_docs import GoogleDocsError
from coe.doc.renderer import render_weekly_doc


@pytest.mark.integration
async def test_ac4_1_full_section_coverage(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4.1: Renderer produces doc with all 5 sections and events.

    Seed coe_events with one row per section. Mock Google APIs. Call
    render_weekly_doc. Assert returned URL is correct and batchUpdate body
    contains section headings and event titles.
    """
    async with session_factory() as session:
        now = datetime.now(UTC)
        since = now - timedelta(days=7)

        # Create a coe_runs row
        run = CoeRun(
            since=since,
            started_at=now,
            status="ok",
            events_ingested=5,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        assert run_id is not None

        # Seed one event per section

        # Section 1: missing_owner
        event1 = CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-001",
            title="Missing owner event",
            severity=CoeSeverity.CRITICAL,
            status="open",
            owner_email=None,
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since - timedelta(days=1),
            updated_at=since - timedelta(days=1),
            coe_review_status="open",
            raw={},
        )
        session.add(event1)

        # Section 2: missing_sla
        event2 = CoeEvent(
            source=Source.WIZ,
            source_id="WIZ-002",
            title="Missing SLA event",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="owner@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=None,
            priority=None,
            opened_at=since - timedelta(days=1),
            updated_at=since - timedelta(days=1),
            coe_review_status="open",
            raw={},
        )
        session.add(event2)

        # Section 3: new
        event3 = CoeEvent(
            source=Source.CROWDSTRIKE,
            source_id="CS-003",
            title="New event",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="owner@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since + timedelta(hours=1),
            updated_at=since + timedelta(hours=1),
            coe_review_status="open",
            raw={},
        )
        session.add(event3)

        # Section 4: changed
        event4 = CoeEvent(
            source=Source.VIBRANIUM,
            source_id="VIBE-004",
            title="Changed event",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="owner@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since - timedelta(days=5),
            updated_at=since + timedelta(hours=1),
            coe_review_status="open",
            raw={},
        )
        session.add(event4)

        # Section 5: recently_resolved
        event5 = CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-005",
            title="Recently resolved event",
            severity=CoeSeverity.CRITICAL,
            status="resolved",
            owner_email="owner@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since - timedelta(days=10),
            updated_at=now - timedelta(hours=1),
            coe_review_status="resolved",
            raw={},
        )
        session.add(event5)

        await session.commit()

        # Capture calls to batch_update and create_doc
        batch_update_calls: list[tuple[Any, ...]] = []
        create_doc_calls: list[tuple[str, str]] = []

        async def mock_batch_update(
            client: Any, document_id: str, requests: list[dict[str, Any]]
        ) -> None:
            batch_update_calls.append((client, document_id, requests))

        async def mock_create_doc(client: Any, name: str, folder_id: str) -> str:
            create_doc_calls.append((name, folder_id))
            return "abc123"

        monkeypatch.setattr("coe.doc.renderer.create_doc", mock_create_doc)
        monkeypatch.setattr("coe.doc.renderer.batch_update", mock_batch_update)

        # Mock build_clients_from_file to avoid auth
        fake_client = MagicMock()
        monkeypatch.setattr("coe.doc.renderer.build_clients_from_file", lambda _: fake_client)

        # Call render_weekly_doc
        settings = get_settings()
        url = await render_weekly_doc(session, settings, run_id)

        # Assertions

        # 1. URL matches expected shape
        assert url == "https://docs.google.com/document/d/abc123/edit"

        # I5: Verify create_doc was called with correct parameters
        assert len(create_doc_calls) == 1
        doc_name, folder_id = create_doc_calls[0]
        # Name should be "COE Prep — Week of YYYY-MM-DD" derived from run.started_at
        expected_name = f"COE Prep — Week of {run.started_at:%Y-%m-%d}"
        assert doc_name == expected_name, f"Expected doc name '{expected_name}', got '{doc_name}'"
        assert folder_id == settings.google_drive_folder_id, (
            f"Expected folder_id '{settings.google_drive_folder_id}', got '{folder_id}'"
        )

        # 2. batch_update was called once
        assert len(batch_update_calls) == 1

        # 3. Extract requests from the call
        _, _, requests = batch_update_calls[0]

        # 4. Find insertText request (contains all the text)
        insert_requests = [r for r in requests if "insertText" in r]
        assert len(insert_requests) > 0

        # Extract text from insertText
        full_text = "\n".join(r["insertText"]["text"] for r in insert_requests)

        # 5. Assert section headings present
        assert "New events" in full_text
        assert "Changed events" in full_text
        assert "Events missing owner" in full_text
        assert "Events missing SLA" in full_text
        assert "Recently resolved" in full_text

        # 6. Assert event titles present in the document
        assert "Missing owner event" in full_text
        assert "Missing SLA event" in full_text
        assert "New event" in full_text
        assert "Changed event" in full_text
        assert "Recently resolved event" in full_text

        # 7. Verify doc_url was written to coe_runs
        result = await session.execute(select(CoeRun).where(CoeRun.id == run_id))
        updated_run = result.scalar_one()
        assert updated_run.doc_url == url


@pytest.mark.integration
async def test_ac4_2_source_links(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4.2: Each event has a source link in the batchUpdate requests.

    Same setup as AC4.1. Inspect updateTextStyle requests for link.url
    matching expected source URL patterns.
    """
    async with session_factory() as session:
        now = datetime.now(UTC)
        since = now - timedelta(days=7)

        # Create a coe_runs row
        run = CoeRun(
            since=since,
            started_at=now,
            status="ok",
            events_ingested=4,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        assert run_id is not None

        # Seed one event per source type
        jira_event = CoeEvent(
            source=Source.JIRA,
            source_id="SEC-123",
            title="Jira issue",
            severity=CoeSeverity.CRITICAL,
            status="open",
            owner_email="alice@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since + timedelta(hours=1),
            updated_at=since + timedelta(hours=1),
            coe_review_status="open",
            raw={},
        )
        session.add(jira_event)

        wiz_event = CoeEvent(
            source=Source.WIZ,
            source_id="wiz-finding-456",
            title="Wiz security finding",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="alice@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since + timedelta(hours=1),
            updated_at=since + timedelta(hours=1),
            coe_review_status="open",
            raw={},
        )
        session.add(wiz_event)

        cs_event = CoeEvent(
            source=Source.CROWDSTRIKE,
            source_id="cs-detection-789",
            title="CrowdStrike detection",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="alice@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since + timedelta(hours=1),
            updated_at=since + timedelta(hours=1),
            coe_review_status="open",
            raw={},
        )
        session.add(cs_event)

        vib_event = CoeEvent(
            source=Source.VIBRANIUM,
            source_id="vib-incident-101",
            title="Vibranium incident",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="alice@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since + timedelta(hours=1),
            updated_at=since + timedelta(hours=1),
            coe_review_status="open",
            raw={},
        )
        session.add(vib_event)

        await session.commit()

        # Capture calls to batch_update and create_doc
        batch_update_calls: list[tuple[Any, ...]] = []
        create_doc_calls: list[tuple[str, str]] = []

        async def mock_batch_update(
            client: Any, document_id: str, requests: list[dict[str, Any]]
        ) -> None:
            batch_update_calls.append((client, document_id, requests))

        async def mock_create_doc(client: Any, name: str, folder_id: str) -> str:
            create_doc_calls.append((name, folder_id))
            return "abc123"

        monkeypatch.setattr("coe.doc.renderer.create_doc", mock_create_doc)
        monkeypatch.setattr("coe.doc.renderer.batch_update", mock_batch_update)

        # Mock build_clients_from_file to avoid auth
        fake_client = MagicMock()
        monkeypatch.setattr("coe.doc.renderer.build_clients_from_file", lambda _: fake_client)

        # Call render_weekly_doc
        settings = get_settings()
        await render_weekly_doc(session, settings, run_id)

        # I5: Verify create_doc was called with correct parameters
        assert len(create_doc_calls) == 1
        doc_name, folder_id = create_doc_calls[0]
        expected_name = f"COE Prep — Week of {run.started_at:%Y-%m-%d}"
        assert doc_name == expected_name
        assert folder_id == settings.google_drive_folder_id

        # Extract requests from batch_update call
        assert len(batch_update_calls) == 1
        _, _, requests = batch_update_calls[0]

        # Find updateTextStyle requests
        link_requests = [r for r in requests if "updateTextStyle" in r]

        # Expected URLs for each source
        expected_urls = {
            "SEC-123": "https://capsule.atlassian.net/browse/SEC-123",
            "wiz-finding-456": "https://app.wiz.io/issues/wiz-finding-456",
            "cs-detection-789": "https://falcon.crowdstrike.com/activity/detections/detail/cs-detection-789",
            "vib-incident-101": "https://vibranium.internal/incidents/vib-incident-101",
        }

        # Verify each source_id has a corresponding link
        found_urls = {}
        for req in link_requests:
            link_url = (
                req.get("updateTextStyle", {}).get("textStyle", {}).get("link", {}).get("url")
            )
            if link_url:
                # Extract source_id from URL
                for source_id in expected_urls:
                    if source_id in link_url:
                        found_urls[source_id] = link_url

        # Assert all sources have links
        for source_id, expected_url in expected_urls.items():
            assert source_id in found_urls, f"Source {source_id} not found in links"
            assert found_urls[source_id] == expected_url, (
                f"URL mismatch for {source_id}: "
                f"expected {expected_url}, got {found_urls[source_id]}"
            )


@pytest.mark.integration
async def test_ac4_3_missing_owner_precedence(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4.3: Event with null owner_email goes to 'missing owner' section, not 'new'.

    Seed two events: one with owner (goes to new), one without owner (goes to
    missing_owner). Assert they appear in correct sections.
    """
    async with session_factory() as session:
        now = datetime.now(UTC)
        since = now - timedelta(days=7)

        # Create a coe_runs row
        run = CoeRun(
            since=since,
            started_at=now,
            status="ok",
            events_ingested=2,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        assert run_id is not None

        # Event A: Has owner, opened after since -> should go to "New events"
        event_a = CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-A",
            title="Event A with owner",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="alice@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since + timedelta(hours=1),  # Recently opened
            updated_at=since + timedelta(hours=1),
            coe_review_status="open",
            raw={},
        )
        session.add(event_a)

        # Event B: No owner, opened after since -> should go to "Events missing owner"
        event_b = CoeEvent(
            source=Source.WIZ,
            source_id="WIZ-B",
            title="Event B missing owner",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email=None,  # NULL owner
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since + timedelta(hours=1),  # Recently opened
            updated_at=since + timedelta(hours=1),
            coe_review_status="open",
            raw={},
        )
        session.add(event_b)

        await session.commit()

        # Capture calls to batch_update and create_doc
        batch_update_calls: list[tuple[Any, ...]] = []
        create_doc_calls: list[tuple[str, str]] = []

        async def mock_batch_update(
            client: Any, document_id: str, requests: list[dict[str, Any]]
        ) -> None:
            batch_update_calls.append((client, document_id, requests))

        async def mock_create_doc(client: Any, name: str, folder_id: str) -> str:
            create_doc_calls.append((name, folder_id))
            return "abc123"

        monkeypatch.setattr("coe.doc.renderer.create_doc", mock_create_doc)
        monkeypatch.setattr("coe.doc.renderer.batch_update", mock_batch_update)

        # Mock build_clients_from_file to avoid auth
        fake_client = MagicMock()
        monkeypatch.setattr("coe.doc.renderer.build_clients_from_file", lambda _: fake_client)

        # Call render_weekly_doc
        settings = get_settings()
        await render_weekly_doc(session, settings, run_id)

        # I5: Verify create_doc was called with correct parameters
        assert len(create_doc_calls) == 1
        doc_name, folder_id = create_doc_calls[0]
        expected_name = f"COE Prep — Week of {run.started_at:%Y-%m-%d}"
        assert doc_name == expected_name
        assert folder_id == settings.google_drive_folder_id

        # Extract text
        assert len(batch_update_calls) == 1
        _, _, requests = batch_update_calls[0]

        insert_requests = [r for r in requests if "insertText" in r]
        full_text = "\n".join(r["insertText"]["text"] for r in insert_requests)

        # Find section indices
        missing_owner_idx = full_text.find("Events missing owner")
        missing_sla_idx = full_text.find("Events missing SLA")
        new_events_idx = full_text.find("New events")

        event_a_idx = full_text.find("Event A with owner")
        event_b_idx = full_text.find("Event B missing owner")

        # Event B should be under "Events missing owner"
        assert missing_owner_idx > 0, "Missing 'Events missing owner' section"
        assert event_b_idx > missing_owner_idx, (
            "Event B should appear after 'Events missing owner' heading"
        )
        assert event_b_idx < missing_sla_idx, "Event B should appear before next section"

        # Event A should be under "New events"
        assert new_events_idx > 0, "Missing 'New events' section"
        assert event_a_idx > new_events_idx, "Event A should appear after 'New events' heading"


@pytest.mark.integration
async def test_ac4_4_api_failure_no_db_mutation(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4.4: Docs API failure raises GoogleDocsError; coe_runs.doc_url stays NULL.

    Mock batch_update to raise GoogleDocsError(500). Call render_weekly_doc.
    Assert it raises GoogleDocsError. Verify coe_runs.doc_url is None after failure.
    """
    async with session_factory() as session:
        now = datetime.now(UTC)
        since = now - timedelta(days=7)

        # Create a coe_runs row
        run = CoeRun(
            since=since,
            started_at=now,
            status="ok",
            events_ingested=1,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        assert run_id is not None

        # Add a minimal event
        event = CoeEvent(
            source=Source.JIRA,
            source_id="JIRA-001",
            title="Test event",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="owner@example.com",
            manager_email=None,
            missing_owner_in_hr=False,
            sla_due_at=now + timedelta(days=1),
            priority=None,
            opened_at=since + timedelta(hours=1),
            updated_at=since + timedelta(hours=1),
            coe_review_status="open",
            raw={},
        )
        session.add(event)
        await session.commit()

        # I3: Capture initial state before rendering
        initial_run_result = await session.execute(select(CoeRun).where(CoeRun.id == run_id))
        initial_run = initial_run_result.scalar_one()
        assert initial_run.doc_url is None  # Verify pre-condition

        # Get initial state of coe_events
        initial_event_result = await session.execute(select(CoeEvent))
        initial_events = {
            e.id: (e.updated_at, e.title) for e in initial_event_result.scalars().all()
        }
        initial_event_count = len(initial_events)

        # Mock batch_update to raise GoogleDocsError
        async def mock_batch_update_fail(
            client: Any, document_id: str, requests: list[dict[str, Any]]
        ) -> None:
            raise GoogleDocsError(500, "internal error")

        async def mock_create_doc(client: Any, name: str, folder_id: str) -> str:
            return "abc123"

        monkeypatch.setattr("coe.doc.renderer.create_doc", mock_create_doc)
        monkeypatch.setattr("coe.doc.renderer.batch_update", mock_batch_update_fail)

        # Mock build_clients_from_file to avoid auth
        fake_client = MagicMock()
        monkeypatch.setattr("coe.doc.renderer.build_clients_from_file", lambda _: fake_client)

        # Call render_weekly_doc - should raise
        settings = get_settings()
        with pytest.raises(GoogleDocsError) as exc_info:
            await render_weekly_doc(session, settings, run_id)

        assert exc_info.value.status == 500

        # Create fresh session to check DB state was not mutated
        async with session_factory() as fresh_session:
            # I3: Verify coe_runs.doc_url is unchanged (still NULL/None)
            result = await fresh_session.execute(select(CoeRun).where(CoeRun.id == run_id))
            run_check = result.scalar_one()

            assert run_check.doc_url is None, (
                "coe_runs.doc_url should remain None after API failure"
            )

            # I3: Verify coe_events rows are unchanged (updated_at byte-identical)
            event_result = await fresh_session.execute(select(CoeEvent))
            current_events = {e.id: (e.updated_at, e.title) for e in event_result.scalars().all()}

            assert len(current_events) == initial_event_count, (
                "coe_events count should be unchanged after API failure"
            )

            for event_id, (initial_ts, initial_title) in initial_events.items():
                assert event_id in current_events, f"Event {event_id} disappeared"
                current_ts, current_title = current_events[event_id]
                assert current_ts == initial_ts, (
                    f"Event {event_id} updated_at changed after API failure"
                )
                assert current_title == initial_title, (
                    f"Event {event_id} title changed after API failure"
                )
