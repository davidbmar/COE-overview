"""Severity normalization and per-source to_coe_event normalizers.

This module provides severity mapping tables for each data source,
a normalize_severity function for looking up severity values,
and per-source normalizer functions that convert raw source records
into unified CoeEvent shapes.
"""

from __future__ import annotations

import structlog

from coe.db.models import CoeEvent, CoeSeverity, Source
from coe.ingest.crowdstrike import CrowdstrikeDetect
from coe.ingest.jira import JiraIssue
from coe.ingest.vibranium import VibraniumIncident
from coe.ingest.wiz import WizIssue

logger = structlog.get_logger(__name__)


# Severity mapping tables per source
SEVERITY_MAP: dict[Source, dict[str, CoeSeverity]] = {
    Source.JIRA: {
        # Jira uses Priority not Severity; map our policy
        "Highest": CoeSeverity.CRITICAL,
        "High": CoeSeverity.HIGH,
    },
    Source.WIZ: {
        "CRITICAL": CoeSeverity.CRITICAL,
        "HIGH": CoeSeverity.HIGH,
    },
    Source.CROWDSTRIKE: {
        # Already pre-bucketed by the client into CRITICAL/HIGH
        "CRITICAL": CoeSeverity.CRITICAL,
        "HIGH": CoeSeverity.HIGH,
    },
    Source.VIBRANIUM: {
        # ⚠ BLOCKER FOR PHASE 3 MERGE: Vibranium severity values below
        # are placeholders. Before this phase is merged, the engineer
        # MUST pull the Vibranium API docs (see Phase 2 Task 6's risk
        # callout), capture the actual severity string values returned
        # by the API, and update this mapping. AC2.1 cannot be claimed
        # passing until this is real data.
        "CRITICAL": CoeSeverity.CRITICAL,
        "HIGH": CoeSeverity.HIGH,
    },
}


def normalize_severity(source: Source, raw_value: str) -> CoeSeverity:
    """Look up a raw severity value for a given source.

    Args:
        source: The Source enum identifying which source this value comes from.
        raw_value: The raw string value from the source (case-sensitive).

    Returns:
        The mapped CoeSeverity enum, or CoeSeverity.UNKNOWN if not found.
        If not found, emits a structlog warning with source and raw_value.
    """
    source_map = SEVERITY_MAP[source]
    if raw_value in source_map:
        return source_map[raw_value]

    # Not found: log warning and return UNKNOWN
    logger.warning("unknown severity value", source=source, raw_value=raw_value)
    return CoeSeverity.UNKNOWN


def jira_to_coe_event(issue: JiraIssue) -> CoeEvent:
    """Convert a Jira issue to a CoeEvent.

    Args:
        issue: A JiraIssue record from the Jira ingest client.

    Returns:
        An unsaved CoeEvent (no id, no last_seen_at).
    """
    severity = normalize_severity(Source.JIRA, issue.priority)

    return CoeEvent(
        source=Source.JIRA,
        source_id=issue.key,
        severity=severity,
        title=issue.summary,
        status=issue.status,
        owner_email=issue.assignee_email,
        manager_email=None,
        missing_owner_in_hr=False,
        opened_at=issue.created,
        updated_at=issue.updated,
        raw=issue.model_dump(mode="json"),
    )


def wiz_to_coe_event(issue: WizIssue) -> CoeEvent:
    """Convert a Wiz issue to a CoeEvent.

    Args:
        issue: A WizIssue record from the Wiz ingest client.

    Returns:
        An unsaved CoeEvent (no id, no last_seen_at).
    """
    severity = normalize_severity(Source.WIZ, issue.severity)
    title = issue.entity_name or "(no entity name)"

    return CoeEvent(
        source=Source.WIZ,
        source_id=issue.id,
        severity=severity,
        title=title,
        status=issue.status,
        owner_email=issue.assignee_email,
        manager_email=None,
        missing_owner_in_hr=False,
        opened_at=issue.created_at,
        updated_at=issue.updated_at,
        raw=issue.model_dump(mode="json"),
    )


def crowdstrike_to_coe_event(detect: CrowdstrikeDetect) -> CoeEvent:
    """Convert a CrowdStrike detection to a CoeEvent.

    Args:
        detect: A CrowdstrikeDetect record from the CrowdStrike ingest client.

    Returns:
        An unsaved CoeEvent (no id, no last_seen_at).
    """
    severity = normalize_severity(Source.CROWDSTRIKE, detect.severity_name)
    title = f"CrowdStrike detection {detect.id}"

    return CoeEvent(
        source=Source.CROWDSTRIKE,
        source_id=detect.id,
        severity=severity,
        title=title,
        status=detect.status,
        owner_email=detect.assigned_to_uid,
        manager_email=None,
        missing_owner_in_hr=False,
        opened_at=detect.last_updated,
        updated_at=detect.last_updated,
        raw=detect.model_dump(mode="json"),
    )


def vibranium_to_coe_event(incident: VibraniumIncident) -> CoeEvent:
    """Convert a Vibranium incident to a CoeEvent.

    Args:
        incident: A VibraniumIncident record from the Vibranium ingest client.

    Returns:
        An unsaved CoeEvent (no id, no last_seen_at).
    """
    severity = normalize_severity(Source.VIBRANIUM, incident.severity)
    title = f"Vibranium incident {incident.id}"

    return CoeEvent(
        source=Source.VIBRANIUM,
        source_id=incident.id,
        severity=severity,
        title=title,
        status=incident.status,
        owner_email=incident.assignee_email,
        manager_email=None,
        missing_owner_in_hr=False,
        opened_at=incident.created_at,
        updated_at=incident.updated_at,
        raw=incident.model_dump(mode="json"),
    )
