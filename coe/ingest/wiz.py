"""Wiz GraphQL API ingest client with OAuth2 token caching."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from coe.config import Settings, get_settings
from coe.ingest.base import request_with_retry


class WizIssue(BaseModel):
    """A Wiz issue record from the GraphQL API."""

    id: str
    severity: str  # String value like "HIGH", "CRITICAL", not a Python Enum
    status: str
    entity_name: str | None
    assignee_email: str | None
    updated_at: datetime
    created_at: datetime
    raw_payload: dict[str, Any]


# Module-level token cache: keyed by client_id, value is (token, expires_at_monotonic)
_token_cache: dict[str, tuple[str, float]] = {}


def _clear_token_cache() -> None:
    """Clear the token cache. Exposed for testing."""
    global _token_cache
    _token_cache.clear()


async def _get_token(client: httpx.AsyncClient, settings: Settings) -> str:
    """Get a cached token or fetch a new one.

    Args:
        client: httpx.AsyncClient for making requests.
        settings: Settings instance with Wiz credentials.

    Returns:
        OAuth2 access token string.

    Raises:
        AuthError: On 401/403 from token endpoint.
        TransientError: On 5xx or transport errors after retries.
    """
    client_id = settings.wiz_client_id
    now = time.monotonic()

    # Check cache
    if client_id in _token_cache:
        cached_token, cached_expires_at = _token_cache[client_id]
        if now < cached_expires_at:
            return cached_token

    # Fetch new token
    body = {
        "client_id": settings.wiz_client_id,
        "client_secret": settings.wiz_client_secret,
        "grant_type": "client_credentials",
        "audience": "wiz-api",
    }

    response = await request_with_retry(
        client,
        "wiz",
        "POST",
        settings.wiz_auth_url,
        data=body,  # Use data= for form-encoded body
    )

    data = response.json()
    token: str = data["access_token"]
    expires_in: int = data["expires_in"]

    # Cache for expires_in - 60 seconds
    expires_at = now + expires_in - 60
    _token_cache[client_id] = (token, expires_at)

    return token


async def fetch_updated_since(
    since: datetime, settings: Settings | None = None
) -> AsyncIterator[WizIssue]:
    """Fetch Wiz issues updated since a given timestamp.

    Queries issues with severity HIGH or CRITICAL, paginating through results
    until exhausted.

    Args:
        since: Minimum updated timestamp (inclusive) in UTC.
        settings: Settings instance; if None, uses get_settings().

    Yields:
        WizIssue models for each issue matching the filter.

    Raises:
        AuthError: On 401/403.
        TransientError: On 5xx or transport errors after retries.
    """
    if settings is None:
        settings = get_settings()

    async with httpx.AsyncClient() as client:
        # Get token
        token = await _get_token(client, settings)
        headers = {"Authorization": f"Bearer {token}"}

        # Build GraphQL query
        graphql_query = """
        query IssuesPage($filter: IssueFilters, $first: Int, $after: String) {
          issues(filterBy: $filter, first: $first, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id severity status updatedAt createdAt
              entitySnapshot { name nativeType }
              projects { id name }
              assignee { email }
            }
          }
        }
        """

        after_cursor: str | None = None

        while True:
            # Build variables
            variables: dict[str, Any] = {
                "filter": {
                    "severity": ["HIGH", "CRITICAL"],
                    "updatedAt": {"after": since.isoformat()},
                },
                "first": 100,
                "after": after_cursor,
            }

            # Make GraphQL request
            body: dict[str, Any] = {
                "query": graphql_query,
                "variables": variables,
            }

            response = await request_with_retry(
                client,
                "wiz",
                "POST",
                settings.wiz_api_url,
                json=body,
                headers=headers,
            )

            data = response.json()

            # Extract issues
            issues_data = data.get("data", {}).get("issues", {})
            for issue_payload in issues_data.get("nodes", []):
                issue = _parse_wiz_issue(issue_payload)
                yield issue

            # Check pagination
            page_info = issues_data.get("pageInfo", {})
            if not page_info.get("hasNextPage", False):
                break

            after_cursor = page_info.get("endCursor")
            if not after_cursor:
                break


def _parse_wiz_issue(payload: dict[str, Any]) -> WizIssue:
    """Parse a Wiz issue from GraphQL response JSON."""
    entity_snapshot = payload.get("entitySnapshot", {})
    assignee = payload.get("assignee")
    assignee_email = assignee.get("email") if assignee else None

    return WizIssue(
        id=payload.get("id", ""),
        severity=payload.get("severity", ""),
        status=payload.get("status", ""),
        entity_name=entity_snapshot.get("name") if entity_snapshot else None,
        assignee_email=assignee_email,
        updated_at=datetime.fromisoformat(payload.get("updatedAt", "").replace("Z", "+00:00")),
        created_at=datetime.fromisoformat(payload.get("createdAt", "").replace("Z", "+00:00")),
        raw_payload=payload,
    )
