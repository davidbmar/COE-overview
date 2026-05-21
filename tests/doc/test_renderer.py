"""Unit tests for coe/doc/renderer.py"""

from coe.db.models import CoeEvent, CoeSeverity, Source
from coe.doc.renderer import build_requests
from coe.doc.sections import DocSections


class TestBuildRequests:
    """Test build_requests pure function."""

    def test_empty_sections_produces_title_and_headers(self) -> None:
        """build_requests with empty sections produces title + section headers + "No events" lines.

        M7 verification: Assert "No events" appears in the text and no link styling is present.
        """

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

        # Extract body text
        body_text = insert_requests[0]["insertText"]["text"]

        # M7: Verify "No events" appears (for each empty section)
        assert body_text.count("No events") == 5, "All 5 sections should have 'No events'"

        # M7: Verify no updateTextStyle requests with links are present
        link_requests = [r for r in requests if "updateTextStyle" in r]
        assert len(link_requests) == 0, "Empty sections should produce no link styling requests"

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


class TestBuildRequestsIndexCorrectness:
    """C2: Test that Docs API indices are correct (not off by len(title))."""

    def test_link_indices_are_correct(self) -> None:
        """C2 verification: For each updateTextStyle.link request, verify the range
        covers exactly the [link] token in the body."""
        from datetime import datetime

        now = datetime.now()
        event = CoeEvent(
            id=1,
            source=Source.JIRA,
            source_id="TEST-123",
            title="Test issue",
            severity=CoeSeverity.CRITICAL,
            status="open",
            owner_email="alice@example.com",
            sla_due_at=None,
            opened_at=now,
            updated_at=now,
            raw={},
        )

        sections = DocSections(
            new=[event],
            changed=[],
            missing_owner=[],
            missing_sla=[],
            recently_resolved=[],
        )

        requests = build_requests("Title", sections)

        # Extract body and link requests
        insert_req = next(r for r in requests if "insertText" in r)
        body = insert_req["insertText"]["text"]

        link_requests = [r for r in requests if "updateTextStyle" in r]
        assert len(link_requests) >= 1, "Should have at least one link"

        # For each link request, verify the range points to [link]
        for req in link_requests:
            start_idx = req["updateTextStyle"]["range"]["startIndex"]
            end_idx = req["updateTextStyle"]["range"]["endIndex"]

            # Extract the text at that range (subtract 1 because Docs indices are 1-based)
            text_at_range = body[start_idx - 1 : end_idx - 1]

            assert text_at_range == "[link]", (
                f"Index range [{start_idx}, {end_idx}) does not cover [link]: got '{text_at_range}'"
            )

    def test_h2_indices_are_correct(self) -> None:
        """C2 verification: For each updateParagraphStyle HEADING_2 request, verify
        the range covers the section heading text."""
        sections = DocSections(
            new=[],
            changed=[],
            missing_owner=[],
            missing_sla=[],
            recently_resolved=[],
        )

        requests = build_requests("Title", sections)

        # Extract body and paragraph style requests
        insert_req = next(r for r in requests if "insertText" in r)
        body = insert_req["insertText"]["text"]

        para_requests = [r for r in requests if "updateParagraphStyle" in r]

        # Find the H2 requests (section headings)
        h2_requests = [
            r
            for r in para_requests
            if r.get("updateParagraphStyle", {}).get("paragraphStyle", {}).get("namedStyleType")
            == "HEADING_2"
        ]

        # Expected section headings
        expected_headings = [
            "New events (",
            "Changed events (",
            "Events missing owner (",
            "Events missing SLA (",
            "Recently resolved (",
        ]

        assert len(h2_requests) == len(expected_headings), (
            f"Expected {len(expected_headings)} H2 requests, got {len(h2_requests)}"
        )

        # For each H2, verify the range covers a section heading
        for req in h2_requests:
            start_idx = req["updateParagraphStyle"]["range"]["startIndex"]
            end_idx = req["updateParagraphStyle"]["range"]["endIndex"]

            # Extract text at that range (Docs indices are 1-based)
            text_at_range = body[start_idx - 1 : end_idx - 1]

            # Verify it starts with one of the expected headings
            starts_with_heading = any(text_at_range.startswith(h) for h in expected_headings)
            assert starts_with_heading, (
                f"H2 range [{start_idx}, {end_idx}) does not cover a section heading: "
                f"got '{text_at_range}'"
            )


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
        """M8: build_requests generates correct link URLs for each source type.

        For each updateTextStyle request with a link, extract and verify the URL
        matches the expected pattern for that source.
        """
        from datetime import datetime

        now = datetime.now()

        # Test all four source types
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

        wiz_event = CoeEvent(
            id=2,
            source=Source.WIZ,
            source_id="wiz-456",
            title="Wiz finding",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="bob@example.com",
            sla_due_at=None,
            opened_at=now,
            updated_at=now,
            raw={},
        )

        cs_event = CoeEvent(
            id=3,
            source=Source.CROWDSTRIKE,
            source_id="cs-789",
            title="CrowdStrike detection",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="charlie@example.com",
            sla_due_at=None,
            opened_at=now,
            updated_at=now,
            raw={},
        )

        vib_event = CoeEvent(
            id=4,
            source=Source.VIBRANIUM,
            source_id="vib-101",
            title="Vibranium incident",
            severity=CoeSeverity.HIGH,
            status="open",
            owner_email="diana@example.com",
            sla_due_at=None,
            opened_at=now,
            updated_at=now,
            raw={},
        )

        sections = DocSections(
            new=[jira_event, wiz_event, cs_event, vib_event],
            changed=[],
            missing_owner=[],
            missing_sla=[],
            recently_resolved=[],
        )

        requests = build_requests(
            "COE Prep",
            sections,
            jira_base_url="https://capsule.atlassian.net",
            vibranium_base_url="https://vibranium.internal",
        )

        # Find updateTextStyle requests (links)
        link_requests = [r for r in requests if "updateTextStyle" in r]
        assert len(link_requests) == 4, "Should have 4 link requests (one per event)"

        # Collect the URLs
        urls: list[str] = []
        for req in link_requests:
            url = req.get("updateTextStyle", {}).get("textStyle", {}).get("link", {}).get("url")
            assert url is not None, f"Link request missing URL: {req}"
            urls.append(url)

        # Verify URLs match expected patterns
        assert any("capsule.atlassian.net/browse/SEC-123" in u for u in urls), (
            "Missing Jira URL with SEC-123"
        )
        assert any("app.wiz.io/issues/wiz-456" in u for u in urls), "Missing Wiz URL with wiz-456"
        assert any("falcon.crowdstrike.com/activity/detections/detail/cs-789" in u for u in urls), (
            "Missing CrowdStrike URL with cs-789"
        )
        assert any("vibranium.internal/incidents/vib-101" in u for u in urls), (
            "Missing Vibranium URL with vib-101"
        )
