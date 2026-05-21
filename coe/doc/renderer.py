"""Weekly prep doc renderer.

Assembles sections from the database, builds Docs API requests, creates the
document, and updates it with styled content and source links.

AC4.1: Data layer integration (sections + google_docs).
AC4.2: Source record links on each event.
AC4.4: Structured error handling for API failures.
"""

from __future__ import annotations

from typing import Any, assert_never

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coe.config import Settings
from coe.db.models import CoeRun, Source
from coe.doc.google_docs import (
    batch_update,
    build_clients_from_file,
    create_doc,
    make_doc_url,
)
from coe.doc.sections import DocSections, build_sections

log = structlog.get_logger()


def build_requests(
    title: str,
    sections: DocSections,
    jira_base_url: str = "https://capsule.atlassian.net",
    vibranium_base_url: str = "https://vibranium.internal",
) -> list[dict[str, Any]]:
    """Build Docs API batchUpdate requests from sections.

    Produces a document structure with:
    - Title (H1)
    - Five sections (H2 headings) with event lines
    - Hyperlinks on [link] tokens pointing to source records

    Tracks cursor position during body assembly to compute correct Docs API indices.
    All indices are absolute Docs coordinates (1-based for insertText location).

    Args:
        title: Document title (e.g., "COE Prep — Week of 2026-05-20")
        sections: DocSections with five bucketed event lists
        jira_base_url: Base URL for Jira browse links
        vibranium_base_url: Base URL for Vibranium incident links

    Returns:
        List of batchUpdate request dicts for docs.documents.batchUpdate
    """
    # Build the body and track cursor positions for styling and links
    body_parts: list[str] = []
    cursor = 0  # Position in the assembled body

    # Track positions for style requests
    heading_positions: list[tuple[str, int, int]] = []  # (heading_text, start, end) for H2s
    link_positions: list[tuple[int, int, str]] = []  # (start, end, url) for [link] tokens

    # Title line (will be H1)
    body_parts.append(title)
    cursor += len(title)
    title_end = cursor

    # Blank line after title (\n\n separates title from first H2 with one blank line)
    body_parts.append("\n\n")
    cursor += 2

    # Helper to add a section with tracking
    def add_section(heading: str, events: list[Any]) -> None:
        nonlocal cursor

        # Section heading (will be H2)
        h2_start = cursor
        body_parts.append(heading)
        cursor += len(heading)
        h2_end = cursor
        heading_positions.append((heading, h2_start, h2_end))

        # Newline after heading
        body_parts.append("\n")
        cursor += 1

        # Event lines
        if not events:
            body_parts.append("No events")
            cursor += len("No events")
        else:
            for idx, event in enumerate(events):
                severity_str = event.severity.value if event.severity else "UNKNOWN"
                owner_str = event.owner_email or "unassigned"
                sla_str = event.sla_due_at.strftime("%Y-%m-%d") if event.sla_due_at else "none"
                line = f"• [{severity_str}] {event.title}  —  owner: {owner_str}   sla: {sla_str}   [link]"

                # Track [link] position
                link_token_pos = line.find("[link]")
                line_start = cursor
                body_parts.append(line)
                cursor += len(line)

                # Separate consecutive event lines with a newline (omit after the last
                # event so the section-trailing newline below isn't duplicated).
                if idx < len(events) - 1:
                    body_parts.append("\n")
                    cursor += 1

                # Record link position (absolute in body)
                link_abs_start = line_start + link_token_pos
                link_abs_end = link_abs_start + len("[link]")
                source_url = _get_source_url(
                    event.source,
                    event.source_id,
                    jira_base_url,
                    vibranium_base_url,
                )
                link_positions.append((link_abs_start, link_abs_end, source_url))

        # Newline after section
        body_parts.append("\n")
        cursor += 1

    # Add all five sections
    add_section(
        "New events (" + str(len(sections.new)) + ")",
        sections.new,
    )
    add_section(
        "Changed events (" + str(len(sections.changed)) + ")",
        sections.changed,
    )
    add_section(
        "Events missing owner (" + str(len(sections.missing_owner)) + ")",
        sections.missing_owner,
    )
    add_section(
        "Events missing SLA (" + str(len(sections.missing_sla)) + ")",
        sections.missing_sla,
    )
    add_section(
        "Recently resolved (" + str(len(sections.recently_resolved)) + ")",
        sections.recently_resolved,
    )

    body = "".join(body_parts)

    # Build Docs API requests
    requests: list[dict[str, Any]] = []

    # 1. Insert the full text at index 1 (after default title block)
    requests.append({
        "insertText": {
            "text": body,
            "location": {"index": 1},
        }
    })

    # 2. Style the title as H1
    # After insertText at index 1, title occupies [1, 1 + len(title))
    requests.append({
        "updateParagraphStyle": {
            "range": {
                "startIndex": 1,
                "endIndex": 1 + title_end,
            },
            "paragraphStyle": {
                "namedStyleType": "HEADING_1",
            },
            "fields": "namedStyleType",
        }
    })

    # 3. Style section headings as H2
    for _heading_text, h2_start, h2_end in heading_positions:
        requests.append({
            "updateParagraphStyle": {
                "range": {
                    "startIndex": 1 + h2_start,
                    "endIndex": 1 + h2_end,
                },
                "paragraphStyle": {
                    "namedStyleType": "HEADING_2",
                },
                "fields": "namedStyleType",
            }
        })

    # 4. Add link styling for each [link] token
    for link_start, link_end, source_url in link_positions:
        requests.append({
            "updateTextStyle": {
                "range": {
                    "startIndex": 1 + link_start,
                    "endIndex": 1 + link_end,
                },
                "textStyle": {"link": {"url": source_url}},
                "fields": "link",
            }
        })

    return requests


def _get_source_url(
    source: Source,
    source_id: str,
    jira_base_url: str,
    vibranium_base_url: str,
) -> str:
    """Generate the source URL based on the source type.

    Args:
        source: Source enum (JIRA, WIZ, CROWDSTRIKE, VIBRANIUM)
        source_id: The source record identifier
        jira_base_url: Base URL for Jira (e.g., https://capsule.atlassian.net)
        vibranium_base_url: Base URL for Vibranium (e.g., https://vibranium.internal)

    Returns:
        Full URL to the source record

    Raises:
        ValueError: If source is not a recognized Source enum value
    """
    if source == Source.JIRA:
        return f"{jira_base_url}/browse/{source_id}"
    elif source == Source.WIZ:
        return f"https://app.wiz.io/issues/{source_id}"
    elif source == Source.CROWDSTRIKE:
        return f"https://falcon.crowdstrike.com/activity/detections/detail/{source_id}"
    elif source == Source.VIBRANIUM:
        return f"{vibranium_base_url}/incidents/{source_id}"
    else:
        # Source is a closed enum, so this should never happen.
        assert_never(source)


async def render_weekly_doc(
    session: AsyncSession,
    settings: Settings,
    run_id: int,
) -> str:
    """Render the weekly prep Google Doc from database state.

    Algorithm:
    1. Load the coe_runs row by run_id
    2. Build the title from run's started_at
    3. Fetch sections using build_sections(session, run.since)
    4. Create authenticated Google clients
    5. Create a new Google Doc in the folder
    6. Build and apply batchUpdate requests
    7. Save the doc URL to coe_runs and commit
    8. Return the doc URL

    Args:
        session: AsyncSession for database access
        settings: Settings with Google credentials and folder ID
        run_id: The coe_runs.id to render

    Returns:
        The shareable Google Docs URL

    Raises:
        GoogleDocsError: If the Google Docs API call fails
    """
    # 1. Load the run row
    run_result = await session.execute(select(CoeRun).where(CoeRun.id == run_id))
    run = run_result.scalar_one()

    # 2. Build the title from started_at
    title = f"COE Prep — Week of {run.started_at:%Y-%m-%d}"

    # 3. Fetch sections
    sections = await build_sections(session, run.since)

    # 4. Create authenticated clients
    client = build_clients_from_file(settings.google_service_account_file)

    # 5. Create a new doc in the folder
    doc_id = await create_doc(client, title, settings.google_drive_folder_id)

    # 6. Build and apply batchUpdate requests
    requests = build_requests(
        title,
        sections,
        jira_base_url=settings.jira_base_url,
        vibranium_base_url=settings.vibranium_base_url,
    )
    await batch_update(client, doc_id, requests)

    # 7. Save the URL and commit
    url = make_doc_url(doc_id)
    run.doc_url = url
    await session.commit()

    log.info("doc_created", run_id=run_id, doc_id=doc_id, url=url)

    # 8. Return the URL
    return url
