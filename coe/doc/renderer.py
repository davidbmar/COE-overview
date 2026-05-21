"""Weekly prep doc renderer.

Assembles sections from the database, builds Docs API requests, creates the
document, and updates it with styled content and source links.

AC4.1: Data layer integration (sections + google_docs).
AC4.2: Source record links on each event.
AC4.4: Structured error handling for API failures.
"""

from __future__ import annotations

from typing import Any

import structlog
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


def build_requests(title: str, sections: DocSections) -> list[dict[str, Any]]:
    """Build Docs API batchUpdate requests from sections.

    Produces a document structure with:
    - Title (H1)
    - Five sections (H2 headings) with event lines
    - Hyperlinks on [link] tokens pointing to source records

    Args:
        title: Document title (e.g., "COE Prep — Week of 2026-05-20")
        sections: DocSections with five bucketed event lists

    Returns:
        List of batchUpdate request dicts for docs.documents.batchUpdate
    """
    # Build the entire doc body as a single string
    lines: list[str] = [title, ""]

    # Helper to add a section
    def add_section(heading: str, events: list[Any], section_name: str) -> None:
        lines.append(heading)
        if not events:
            lines.append("No events")
        else:
            for event in events:
                severity_str = event.severity.value if event.severity else "UNKNOWN"
                owner_str = event.owner_email or "unassigned"
                sla_str = event.sla_due_at.strftime("%Y-%m-%d") if event.sla_due_at else "none"
                line = f"• [{severity_str}] {event.title}  —  owner: {owner_str}   sla: {sla_str}   [link]"
                lines.append(line)
        lines.append("")

    # Add all five sections
    add_section(
        "New events (" + str(len(sections.new)) + ")",
        sections.new,
        "new",
    )
    add_section(
        "Changed events (" + str(len(sections.changed)) + ")",
        sections.changed,
        "changed",
    )
    add_section(
        "Events missing owner (" + str(len(sections.missing_owner)) + ")",
        sections.missing_owner,
        "missing_owner",
    )
    add_section(
        "Events missing SLA (" + str(len(sections.missing_sla)) + ")",
        sections.missing_sla,
        "missing_sla",
    )
    add_section(
        "Recently resolved (" + str(len(sections.recently_resolved)) + ")",
        sections.recently_resolved,
        "recently_resolved",
    )

    body = "\n".join(lines)

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
    title_end_index = len(title)
    requests.append({
        "updateParagraphStyle": {
            "range": {
                "startIndex": 1,
                "endIndex": title_end_index + 1,
            },
            "paragraphStyle": {
                "namedStyleType": "HEADING_1",
            },
            "fields": "namedStyleType",
        }
    })

    # 3. Find and style section headers + links
    # Parse through the body to find indices of section headers and [link] tokens
    current_index = title_end_index + 2  # After title + newline

    for section_heading in [
        "New events (",
        "Changed events (",
        "Events missing owner (",
        "Events missing SLA (",
        "Recently resolved (",
    ]:
        section_start = body.find(section_heading, current_index - title_end_index - 2)
        if section_start >= 0:
            # Adjust to absolute index in Docs (1-based after insertText)
            absolute_start = section_start + title_end_index + 2
            # Find the end of the line (the closing parenthesis and count)
            line_end_in_body = body.find("\n", section_start)
            line_end_local = line_end_in_body - section_start
            absolute_end = absolute_start + line_end_local

            requests.append({
                "updateParagraphStyle": {
                    "range": {
                        "startIndex": absolute_start,
                        "endIndex": absolute_end,
                    },
                    "paragraphStyle": {
                        "namedStyleType": "HEADING_2",
                    },
                    "fields": "namedStyleType",
                }
            })

    # 4. Add link styling for each event
    # Helper to find all event [link] tokens and create updateTextStyle requests
    all_events = (
        sections.new
        + sections.changed
        + sections.missing_owner
        + sections.missing_sla
        + sections.recently_resolved
    )

    for event in all_events:
        # Generate the source URL based on the source type
        source_url = _get_source_url(event.source, event.source_id)

        # Find the [link] token for this event in the body
        # For now, we'll use a simple linear search through the text
        # (A production system might maintain a map during construction)
        # Find the line containing the event title
        event_line_search = f"[{event.severity.value}] {event.title}"
        line_start = body.find(event_line_search)
        if line_start >= 0:
            # Find [link] token on this line
            line_end = body.find("\n", line_start)
            line_content = body[line_start:line_end]
            link_pos_in_line = line_content.find("[link]")
            if link_pos_in_line >= 0:
                # Convert to absolute Docs index
                link_start = line_start + link_pos_in_line + title_end_index + 2
                link_end = link_start + len("[link]")

                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": link_start,
                            "endIndex": link_end,
                        },
                        "textStyle": {
                            "link": {
                                "url": source_url,
                            }
                        },
                        "fields": "link",
                    }
                })

    return requests


def _get_source_url(source: Source, source_id: str) -> str:
    """Generate the source URL based on the source type.

    Args:
        source: Source enum (JIRA, WIZ, CROWDSTRIKE, VIBRANIUM)
        source_id: The source record identifier

    Returns:
        Full URL to the source record
    """
    if source == Source.JIRA:
        # Will be filled in by the caller with jira_base_url from settings
        return f"https://capsule.atlassian.net/browse/{source_id}"
    elif source == Source.WIZ:
        return f"https://app.wiz.io/issues/{source_id}"
    elif source == Source.CROWDSTRIKE:
        return f"https://falcon.crowdstrike.com/activity/detections/detail/{source_id}"
    elif source == Source.VIBRANIUM:
        # Will be filled in by the caller with vibranium_base_url from settings
        return f"https://vibranium.internal/incidents/{source_id}"
    else:
        return ""


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
    from sqlalchemy import select

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
    requests = build_requests(title, sections)
    await batch_update(client, doc_id, requests)

    # 7. Save the URL and commit
    url = make_doc_url(doc_id)
    run.doc_url = url
    await session.commit()

    log.info("doc_created", run_id=run_id, doc_id=doc_id, url=url)

    # 8. Return the URL
    return url
