# Placeholder contract — endpoint paths and response shape to be replaced when
# internal Vibranium docs land.
"""Vibranium incident management API ingest client."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from coe.config import Settings, get_settings
from coe.ingest.base import request_with_retry


class VibraniumIncident(BaseModel):
    """A Vibranium incident record."""

    id: str
    severity: str  # Raw string from Vibranium, not a Python Enum
    status: str
    assignee_email: str | None
    updated_at: datetime
    created_at: datetime
    raw_payload: dict[str, Any]


async def fetch_updated_since(
    since: datetime,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[VibraniumIncident]:
    """Fetch Vibranium incidents updated since a given timestamp.

    Queries incidents with severity high or critical, paginating through results.

    Args:
        since: Minimum updated timestamp (inclusive) in UTC.
        settings: Settings instance; if None, uses get_settings().
        client: httpx.AsyncClient for making requests. If None, creates one
            internally. If provided, the caller is responsible for closing it.

    Yields:
        VibraniumIncident models for each incident matching the filter.

    Raises:
        AuthError: On 401/403.
        TransientError: On 5xx or transport errors after retries.
    """
    if settings is None:
        settings = get_settings()

    async def _fetch_paginated(http_client: httpx.AsyncClient) -> AsyncIterator[VibraniumIncident]:
        """Inner generator that performs the paginated fetch using the provided client."""
        headers = {"Authorization": f"Bearer {settings.vibranium_api_token}"}

        cursor: str | None = None

        while True:
            params: dict[str, str | None] = {
                "since": since.isoformat(),
                "severity": "high,critical",
                "cursor": cursor,
            }
            # Remove None values from params
            params = {k: v for k, v in params.items() if v is not None}

            response = await request_with_retry(
                http_client,
                "vibranium",
                "GET",
                f"{settings.vibranium_base_url}/incidents",
                params=params,
                headers=headers,
            )

            data = response.json()
            incidents_data = data.get("data", [])

            for incident_payload in incidents_data:
                incident = _parse_vibranium_incident(incident_payload)
                yield incident

            # Check pagination
            next_cursor = data.get("next_cursor")
            if not next_cursor:
                # No more pages
                break

            cursor = next_cursor

    if client is not None:
        # Use provided client (caller is responsible for cleanup)
        async for incident in _fetch_paginated(client):
            yield incident
    else:
        # Create and manage our own client
        async with httpx.AsyncClient() as own_client:
            async for incident in _fetch_paginated(own_client):
                yield incident


def _parse_vibranium_incident(payload: dict[str, Any]) -> VibraniumIncident:
    """Parse a Vibranium incident from API response JSON.

    Raises ValidationError if required timestamp fields are missing.
    """
    # Parse timestamps from ISO strings (required fields; raises ValidationError if missing)
    updated_at_str = payload.get("updated_at", "")
    created_at_str = payload.get("created_at", "")

    updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))

    return VibraniumIncident(
        id=payload.get("id", ""),
        severity=payload.get("severity", ""),  # Raw string, not enum
        status=payload.get("status", ""),
        assignee_email=payload.get("assignee_email"),
        updated_at=updated_at,
        created_at=created_at,
        raw_payload=payload,
    )
