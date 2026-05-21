"""CrowdStrike Falcon API ingest client with OAuth2 token caching."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from coe.config import Settings, get_settings
from coe.ingest.base import request_with_retry


class CrowdstrikeDetect(BaseModel):
    """A CrowdStrike detection record."""

    id: str
    max_severity: int
    severity_name: str  # Derived from max_severity
    status: str
    last_updated: datetime
    assigned_to_uid: str | None
    raw_payload: dict[str, Any]


# Module-level token cache: keyed by client_id, value is (token, expires_at_monotonic)
_token_cache: dict[str, tuple[str, float]] = {}


def _clear_token_cache() -> None:
    """Clear the token cache. Exposed for testing."""
    global _token_cache
    _token_cache.clear()


async def _get_token(
    client: httpx.AsyncClient | None = None, settings: Settings | None = None
) -> str:
    """Get a cached token or fetch a new one.

    Args:
        client: httpx.AsyncClient for making requests. If None, creates one internally.
        settings: Settings instance with CrowdStrike credentials. If None, uses get_settings().

    Returns:
        OAuth2 access token string.

    Raises:
        AuthError: On 401/403 from token endpoint.
        TransientError: On 5xx or transport errors after retries.
    """
    if settings is None:
        settings = get_settings()

    client_id = settings.crowdstrike_client_id
    now = time.monotonic()

    # Check cache
    if client_id in _token_cache:
        cached_token, cached_expires_at = _token_cache[client_id]
        if now < cached_expires_at:
            return cached_token

    # Fetch new token
    body = {
        "client_id": settings.crowdstrike_client_id,
        "client_secret": settings.crowdstrike_client_secret,
        "grant_type": "client_credentials",
    }

    async def _do_request(http_client: httpx.AsyncClient) -> str:
        """Inner function to make the token request."""
        response = await request_with_retry(
            http_client,
            "crowdstrike",
            "POST",
            f"{settings.crowdstrike_base_url}/oauth2/token",
            data=body,
        )

        data = response.json()
        token: str = data["access_token"]
        expires_in: int = data["expires_in"]

        # Cache for expires_in - 60 seconds
        expires_at = now + expires_in - 60
        _token_cache[client_id] = (token, expires_at)

        return token

    if client is not None:
        return await _do_request(client)
    else:
        async with httpx.AsyncClient() as temp_client:
            return await _do_request(temp_client)


def _compute_severity_name(max_severity: int) -> str:
    """Compute severity_name from max_severity numeric value.

    Args:
        max_severity: Numeric severity value (0-100).

    Returns:
        "CRITICAL" if >= 90, "HIGH" if >= 70, else "MEDIUM".
    """
    if max_severity >= 90:
        return "CRITICAL"
    elif max_severity >= 70:
        return "HIGH"
    else:
        return "MEDIUM"


async def fetch_updated_since(
    since: datetime,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[CrowdstrikeDetect]:
    """Fetch CrowdStrike detections updated since a given timestamp.

    Queries detections with max_severity >= 70, paginating through results.

    Args:
        since: Minimum last_updated timestamp (inclusive) in UTC.
        settings: Settings instance; if None, uses get_settings().
        client: httpx.AsyncClient for making requests. If None, creates one
            internally. If provided, the caller is responsible for closing it.

    Yields:
        CrowdstrikeDetect models for each detection matching the filter.

    Raises:
        AuthError: On 401/403.
        TransientError: On 5xx or transport errors after retries.
    """
    if settings is None:
        settings = get_settings()

    async def _fetch_paginated(http_client: httpx.AsyncClient) -> AsyncIterator[CrowdstrikeDetect]:
        """Inner generator that performs the paginated fetch using the provided client."""
        # Get token
        token = await _get_token(http_client, settings)
        headers = {"Authorization": f"Bearer {token}"}

        # Iterate through pages via offset pagination
        offset = 0
        limit = 1000

        while True:
            # Step 2: Query IDs
            filter_str = f"max_severity:>=70 last_updated:>='{since.isoformat()}'"

            response = await request_with_retry(
                http_client,
                "crowdstrike",
                "GET",
                f"{settings.crowdstrike_base_url}/detects/queries/detects/v1",
                params={"filter": filter_str, "limit": limit, "offset": offset},
                headers=headers,
            )

            data = response.json()
            detection_ids: list[str] = data.get("resources", [])

            if not detection_ids:
                # No more pages
                break

            # Step 3: Fetch summaries for this batch (batched in up to 1000)
            summaries_response = await request_with_retry(
                http_client,
                "crowdstrike",
                "POST",
                f"{settings.crowdstrike_base_url}/detects/entities/summaries/GET/v1",
                json={"ids": detection_ids},
                headers=headers,
            )

            summaries_data = summaries_response.json()
            for detect_payload in summaries_data.get("resources", []):
                detect = _parse_crowdstrike_detect(detect_payload)
                yield detect

            # Check if there are more pages
            if len(detection_ids) < limit:
                # Fewer results than limit; no more pages
                break

            # Move to next page
            offset += limit

    if client is not None:
        # Use provided client (caller is responsible for cleanup)
        async for detect in _fetch_paginated(client):
            yield detect
    else:
        # Create and manage our own client
        async with httpx.AsyncClient() as own_client:
            async for detect in _fetch_paginated(own_client):
                yield detect


def _parse_crowdstrike_detect(payload: dict[str, Any]) -> CrowdstrikeDetect:
    """Parse a CrowdStrike detection from API response JSON."""
    max_severity = int(payload.get("max_severity", 0))
    severity_name = _compute_severity_name(max_severity)

    # Parse last_updated from ISO string (required field; raises ValidationError if missing)
    last_updated_str = payload.get("last_updated", "")
    last_updated = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))

    return CrowdstrikeDetect(
        id=payload.get("detection_id", ""),
        max_severity=max_severity,
        severity_name=severity_name,
        status=payload.get("status", ""),
        last_updated=last_updated,
        assigned_to_uid=payload.get("assigned_to_uid"),
        raw_payload=payload,
    )
