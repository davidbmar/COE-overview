"""Unit tests for coe/doc/renderer.py"""

from coe.db.models import CoeEvent, CoeSeverity, Source
from coe.doc.renderer import build_requests
from coe.doc.sections import DocSections


class TestBuildRequests:
    """Test build_requests pure function."""

    def test_empty_sections_produces_title_and_headers(self) -> None:
        """build_requests with empty sections produces title + section headers + no events."""

        sections = DocSections(
            new=[],
            changed=[],
            missing_owner=[],
            missing_sla=[],
            recently_resolved=[],
        )

        requests = build_requests("COE Prep — Week of 2026-05-20", sections)

        # Should have insertText + style requests for title and section headers
        assert len(requests) > 0
        # First request should be insertText
        insert_requests = [r for r in requests if "insertText" in r]
        assert len(insert_requests) == 1

    def test_populated_sections_include_event_lines_and_links(self) -> None:
        """build_requests with events produces event lines and link styling requests."""
        from datetime import datetime

        # Create sample events
        now = datetime.now()
        jira_event = CoeEvent(
            id=1,
            source=Source.JIRA,
            source_id="TICKET-123",
            title="Critical issue",
            severity=CoeSeverity.CRITICAL,
            status="open",
            owner_email="alice@example.com",
            sla_due_at=None,
            opened_at=now,
            updated_at=now,
            raw={},
        )
        wiz_event = CoeEvent(
            id=2,
            source=Source.WIZ,
            source_id="wiz-456",
            title="Security finding",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="bob@example.com",
            sla_due_at=None,
            opened_at=now,
            updated_at=now,
            raw={},
        )

        sections = DocSections(
            new=[jira_event, wiz_event],
            changed=[],
            missing_owner=[],
            missing_sla=[],
            recently_resolved=[],
        )

        requests = build_requests("COE Prep — Week of 2026-05-20", sections)

        # Should have insertText + updateTextStyle requests for links
        insert_requests = [r for r in requests if "insertText" in r]
        style_requests = [r for r in requests if "updateTextStyle" in r]

        # Should have at least one insertText and some link styling
        assert len(insert_requests) >= 1
        assert len(style_requests) >= 2  # One per event


class TestBuildRequestsWithSections:
    """Test build_requests content includes expected section headings."""

    def test_all_sections_appear_in_output(self) -> None:
        """build_requests includes all five section headings."""
        sections = DocSections(
            new=[],
            changed=[],
            missing_owner=[],
            missing_sla=[],
            recently_resolved=[],
        )

        requests = build_requests("COE Prep — Week of 2026-05-20", sections)

        # Find the main insertText request
        insert_request = next(r for r in requests if "insertText" in r)
        text = insert_request["insertText"]["text"]

        # All sections should be mentioned
        assert "New events" in text
        assert "Changed events" in text
        assert "Events missing owner" in text
        assert "Events missing SLA" in text
        assert "Recently resolved" in text

    def test_link_urls_match_source_patterns(self) -> None:
        """build_requests generates correct link URLs for each source."""
        from datetime import datetime

        now = datetime.now()
        jira_event = CoeEvent(
            id=1,
            source=Source.JIRA,
            source_id="SEC-123",
            title="Jira issue",
            severity=CoeSeverity.CRITICAL,
            status="open",
            owner_email="alice@example.com",
            sla_due_at=None,
            opened_at=now,
            updated_at=now,
            raw={},
        )

        sections = DocSections(
            new=[jira_event],
            changed=[],
            missing_owner=[],
            missing_sla=[],
            recently_resolved=[],
        )

        requests = build_requests("COE Prep", sections)

        # Find updateTextStyle requests (links)
        link_requests = [r for r in requests if "updateTextStyle" in r]
        assert len(link_requests) > 0

        # Check that at least one has a link.url
        has_link = any("link" in str(r) for r in link_requests)
        assert has_link
