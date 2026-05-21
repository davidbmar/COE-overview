"""Jira REST API v3 ingest client."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel

from coe.config import Settings, get_settings
from coe.ingest.base import request_with_retry


class JiraIssue(BaseModel):
    """A Jira issue record from the REST API."""

    key: str
    summary: str
    priority: str
    status: str
    assignee_email: str | None
    updated: datetime
    created: datetime
    raw_payload: dict[str, Any]


async def fetch_updated_since(
    since: datetime,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[JiraIssue]:
    """Fetch Jira issues updated since a given timestamp.

    Queries the configured COE project allowlist, paginating through results
    until exhausted.

    Args:
        since: Minimum updated timestamp (inclusive) in UTC.
        settings: Settings instance; if None, uses get_settings().
        client: httpx.AsyncClient for making requests. If None, creates one
            internally. If provided, the caller is responsible for closing it.

    Yields:
        JiraIssue models for each issue matching the filter.

    Raises:
        AuthError: On 401/403.
        TransientError: On 5xx or transport errors after retries.
    """
    if settings is None:
        settings = get_settings()

    async def _fetch_paginated(http_client: httpx.AsyncClient) -> AsyncIterator[JiraIssue]:
        """Inner generator that performs the paginated fetch using the provided client."""
        # Build JQL filter
        projects_str = ", ".join(f'"{p}"' for p in settings.jira_projects)
        # Convert to UTC explicitly and format with second precision and timezone offset
        since_utc = since.astimezone(UTC)
        since_iso = since_utc.strftime("%Y-%m-%d %H:%M:%S %z")
        jql = f'project IN ({projects_str}) AND updated >= "{since_iso}"'

        # Build basic auth header
        auth_str = f"{settings.jira_user_email}:{settings.jira_api_token}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        headers = {"Authorization": f"Basic {auth_b64}"}

        next_page_token: str | None = None
        while True:
            body: dict[str, Any] = {
                "jql": jql,
                "maxResults": 100,
            }
            if next_page_token:
                body["nextPageToken"] = next_page_token

            # Make request with retry logic
            response = await request_with_retry(
                http_client,
                "jira",
                "POST",
                f"{settings.jira_base_url}/rest/api/3/search/jql",
                json=body,
                headers=headers,
            )

            data = response.json()

            # Yield each issue
            for issue_payload in data.get("issues", []):
                issue = _parse_jira_issue(issue_payload)
                yield issue

            # Check pagination
            if data.get("isLast", True):
                break

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

    if client is not None:
        # Use provided client (caller is responsible for cleanup)
        async for issue in _fetch_paginated(client):
            yield issue
    else:
        # Create and manage our own client
        async with httpx.AsyncClient() as own_client:
            async for issue in _fetch_paginated(own_client):
                yield issue


def _parse_jira_issue(payload: dict[str, Any]) -> JiraIssue:
    """Parse a Jira issue from API response JSON."""
    fields = payload.get("fields", {})
    assignee = fields.get("assignee")
    assignee_email = assignee.get("emailAddress") if assignee else None

    return JiraIssue(
        key=payload.get("key", ""),
        summary=fields.get("summary", ""),
        priority=fields.get("priority", {}).get("name", ""),
        status=fields.get("status", {}).get("name", ""),
        assignee_email=assignee_email,
        updated=datetime.fromisoformat(
            fields.get("updated", "").replace("Z", "+00:00").replace(".000+0000", "+00:00")
        ),
        created=datetime.fromisoformat(
            fields.get("created", "").replace("Z", "+00:00").replace(".000+0000", "+00:00")
        ),
        raw_payload=payload,
    )
