"""Tests for coe.normalize module — severity mapping and per-source normalizers.

Verifies AC2.1, AC2.2.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from structlog.testing import capture_logs

from coe.db.models import CoeSeverity, Source
from coe.ingest.crowdstrike import CrowdstrikeDetect
from coe.ingest.jira import JiraIssue
from coe.ingest.vibranium import VibraniumIncident
from coe.ingest.wiz import WizIssue
from coe.normalize import (
    SEVERITY_MAP,
    crowdstrike_to_coe_event,
    jira_to_coe_event,
    normalize_severity,
    vibranium_to_coe_event,
    wiz_to_coe_event,
)

pytestmark = pytest.mark.unit


class TestSeverityMap:
    """Tests for SEVERITY_MAP constant."""

    def test_severity_map_has_all_sources(self) -> None:
        """SEVERITY_MAP includes entries for all Sources."""
        assert Source.JIRA in SEVERITY_MAP
        assert Source.WIZ in SEVERITY_MAP
        assert Source.CROWDSTRIKE in SEVERITY_MAP
        assert Source.VIBRANIUM in SEVERITY_MAP

    def test_severity_map_values_are_severity_enums(self) -> None:
        """SEVERITY_MAP values are CoeSeverity enums."""
        for source_map in SEVERITY_MAP.values():
            for raw_val, severity in source_map.items():
                assert isinstance(raw_val, str)
                assert isinstance(severity, CoeSeverity)


class TestNormalizeSeverity:
    """Tests for normalize_severity function."""

    @pytest.mark.parametrize(
        "source,raw_value,expected",
        [
            (Source.JIRA, "Highest", CoeSeverity.CRITICAL),
            (Source.JIRA, "High", CoeSeverity.HIGH),
            (Source.WIZ, "CRITICAL", CoeSeverity.CRITICAL),
            (Source.WIZ, "HIGH", CoeSeverity.HIGH),
            (Source.CROWDSTRIKE, "CRITICAL", CoeSeverity.CRITICAL),
            (Source.CROWDSTRIKE, "HIGH", CoeSeverity.HIGH),
            (Source.VIBRANIUM, "CRITICAL", CoeSeverity.CRITICAL),
            (Source.VIBRANIUM, "HIGH", CoeSeverity.HIGH),
        ],
    )
    def test_normalize_severity_known_values(
        self, source: Source, raw_value: str, expected: CoeSeverity
    ) -> None:
        """AC2.1: Known severity values map to expected CoeSeverity."""
        result = normalize_severity(source, raw_value)
        assert result == expected

    def test_normalize_severity_unknown_value_logs_warning(self) -> None:
        """AC2.2: Unknown severity value returns UNKNOWN and logs warning."""
        with capture_logs() as cap_logs:
            result = normalize_severity(Source.JIRA, "Low")

        assert result == CoeSeverity.UNKNOWN
        # Filter for warning level to isolate the message
        warnings = [r for r in cap_logs if r.get("log_level") == "warning"]
        assert len(warnings) == 1
        assert warnings[0]["source"] == Source.JIRA
        assert warnings[0]["raw_value"] == "Low"
        assert warnings[0]["event"] == "unknown severity value"

    def test_normalize_severity_case_sensitive(self) -> None:
        """Severity lookup is case-sensitive; wrong case returns UNKNOWN."""
        with capture_logs() as cap_logs:
            result = normalize_severity(Source.JIRA, "highest")  # lowercase

        assert result == CoeSeverity.UNKNOWN
        warnings = [r for r in cap_logs if r.get("log_level") == "warning"]
        assert len(warnings) == 1
        assert warnings[0]["raw_value"] == "highest"


class TestJiraToCoEEvent:
    """Tests for jira_to_coe_event normalizer."""

    def test_jira_to_coe_event_basic(self) -> None:
        """jira_to_coe_event produces CoeEvent with expected fields."""
        issue = JiraIssue(
            key="SEC-123",
            summary="Fix security issue",
            priority="Highest",
            status="In Progress",
            assignee_email="alice@example.com",
            updated=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            created=datetime(2026, 5, 20, 10, 0, 0, tzinfo=UTC),
            raw_payload={"key": "SEC-123", "fields": {"summary": "Fix security issue"}},
        )

        event = jira_to_coe_event(issue)

        assert event.source == Source.JIRA
        assert event.source_id == "SEC-123"
        assert event.severity == CoeSeverity.CRITICAL
        assert event.title == "Fix security issue"
        assert event.status == "In Progress"
        assert event.owner_email == "alice@example.com"
        assert event.opened_at == datetime(2026, 5, 20, 10, 0, 0, tzinfo=UTC)
        assert event.updated_at == datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC)
        assert event.manager_email is None
        assert event.missing_owner_in_hr is False
        # id and last_seen_at should not be set (autoincrement/default)
        assert event.id is None

    def test_jira_to_coe_event_raw_roundtrip(self) -> None:
        """jira_to_coe_event.raw equals issue.model_dump(mode='json')."""
        raw_payload = {"key": "SEC-123", "fields": {"summary": "Test"}}
        issue = JiraIssue(
            key="SEC-123",
            summary="Test",
            priority="High",
            status="Open",
            assignee_email="bob@example.com",
            updated=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            created=datetime(2026, 5, 20, 10, 0, 0, tzinfo=UTC),
            raw_payload=raw_payload,
        )

        event = jira_to_coe_event(issue)

        assert event.raw == issue.model_dump(mode="json")

    def test_jira_to_coe_event_empty_summary(self) -> None:
        """jira_to_coe_event uses 'Jira <key>' when summary is empty."""
        issue = JiraIssue(
            key="SEC-999",
            summary="",
            priority="High",
            status="Open",
            assignee_email="alice@example.com",
            updated=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            created=datetime(2026, 5, 20, 10, 0, 0, tzinfo=UTC),
            raw_payload={"key": "SEC-999"},
        )

        event = jira_to_coe_event(issue)

        assert event.title == "Jira SEC-999"


class TestWizToCoEEvent:
    """Tests for wiz_to_coe_event normalizer."""

    def test_wiz_to_coe_event_basic(self) -> None:
        """wiz_to_coe_event produces CoeEvent with expected fields."""
        issue = WizIssue(
            id="wiz-456",
            severity="CRITICAL",
            status="OPEN",
            entity_name="prod-server-01",
            assignee_email="carol@example.com",
            updated_at=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            created_at=datetime(2026, 5, 19, 10, 0, 0, tzinfo=UTC),
            raw_payload={"id": "wiz-456", "severity": "CRITICAL"},
        )

        event = wiz_to_coe_event(issue)

        assert event.source == Source.WIZ
        assert event.source_id == "wiz-456"
        assert event.severity == CoeSeverity.CRITICAL
        assert event.title == "prod-server-01"
        assert event.status == "OPEN"
        assert event.owner_email == "carol@example.com"
        assert event.opened_at == datetime(2026, 5, 19, 10, 0, 0, tzinfo=UTC)
        assert event.updated_at == datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC)
        assert event.manager_email is None
        assert event.missing_owner_in_hr is False

    def test_wiz_to_coe_event_no_entity_name(self) -> None:
        """wiz_to_coe_event uses '(no entity name)' if entity_name is None."""
        issue = WizIssue(
            id="wiz-789",
            severity="HIGH",
            status="RESOLVED",
            entity_name=None,
            assignee_email="dave@example.com",
            updated_at=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            created_at=datetime(2026, 5, 19, 10, 0, 0, tzinfo=UTC),
            raw_payload={"id": "wiz-789"},
        )

        event = wiz_to_coe_event(issue)

        assert event.title == "(no entity name)"

    def test_wiz_to_coe_event_raw_roundtrip(self) -> None:
        """wiz_to_coe_event.raw equals issue.model_dump(mode='json')."""
        issue = WizIssue(
            id="wiz-999",
            severity="HIGH",
            status="OPEN",
            entity_name="test-entity",
            assignee_email="eve@example.com",
            updated_at=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            created_at=datetime(2026, 5, 19, 10, 0, 0, tzinfo=UTC),
            raw_payload={"id": "wiz-999"},
        )

        event = wiz_to_coe_event(issue)

        assert event.raw == issue.model_dump(mode="json")


class TestCrowdstrikeToCoEEvent:
    """Tests for crowdstrike_to_coe_event normalizer."""

    def test_crowdstrike_to_coe_event_basic(self) -> None:
        """crowdstrike_to_coe_event produces CoeEvent with expected fields."""
        detect = CrowdstrikeDetect(
            id="cs-detect-001",
            max_severity=95,
            severity_name="CRITICAL",
            status="new",
            last_updated=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            assigned_to_uid="user-123",
            raw_payload={"detection_id": "cs-detect-001", "status": "new"},
        )

        event = crowdstrike_to_coe_event(detect)

        assert event.source == Source.CROWDSTRIKE
        assert event.source_id == "cs-detect-001"
        assert event.severity == CoeSeverity.CRITICAL
        assert "cs-detect-001" in event.title or "CrowdStrike" in event.title
        assert event.status == "new"
        assert event.owner_email == "user-123"
        assert event.opened_at == datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC)
        assert event.updated_at == datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC)
        assert event.manager_email is None
        assert event.missing_owner_in_hr is False

    def test_crowdstrike_to_coe_event_raw_roundtrip(self) -> None:
        """crowdstrike_to_coe_event.raw equals detect.model_dump(mode='json')."""
        detect = CrowdstrikeDetect(
            id="cs-detect-002",
            max_severity=75,
            severity_name="HIGH",
            status="new",
            last_updated=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            assigned_to_uid="user-456",
            raw_payload={"detection_id": "cs-detect-002"},
        )

        event = crowdstrike_to_coe_event(detect)

        assert event.raw == detect.model_dump(mode="json")


class TestVibraniumToCoEEvent:
    """Tests for vibranium_to_coe_event normalizer."""

    def test_vibranium_to_coe_event_basic(self) -> None:
        """vibranium_to_coe_event produces CoeEvent with expected fields."""
        incident = VibraniumIncident(
            id="vib-incident-001",
            severity="CRITICAL",
            status="open",
            assignee_email="frank@example.com",
            updated_at=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            created_at=datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC),
            raw_payload={"id": "vib-incident-001", "severity": "CRITICAL"},
        )

        event = vibranium_to_coe_event(incident)

        assert event.source == Source.VIBRANIUM
        assert event.source_id == "vib-incident-001"
        assert event.severity == CoeSeverity.CRITICAL
        assert "vib-incident-001" in event.title or "Vibranium" in event.title
        assert event.status == "open"
        assert event.owner_email == "frank@example.com"
        assert event.opened_at == datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC)
        assert event.updated_at == datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC)
        assert event.manager_email is None
        assert event.missing_owner_in_hr is False

    def test_vibranium_to_coe_event_raw_roundtrip(self) -> None:
        """vibranium_to_coe_event.raw equals incident.model_dump(mode='json')."""
        incident = VibraniumIncident(
            id="vib-incident-002",
            severity="HIGH",
            status="resolved",
            assignee_email="grace@example.com",
            updated_at=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            created_at=datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC),
            raw_payload={"id": "vib-incident-002"},
        )

        event = vibranium_to_coe_event(incident)

        assert event.raw == incident.model_dump(mode="json")
