"""Internal HR service ingest client for employee directory.

Placeholder contract — endpoint paths and response shape to be replaced when
internal HR docs land.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import BaseModel

from coe.config import Settings, get_settings
from coe.ingest.base import request_with_retry


class Employee(BaseModel):
    """An employee record from the internal HR service."""

    email: str
    manager_email: str | None
    org_path: str | None
    is_active: bool


async def fetch_all_active_employees(
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[Employee]:
    """Fetch all active employees from the internal HR service.

    The full employee directory is expected to be small enough to pull as a
    single dump per run (no delta semantics). If pagination is exposed,
    iterates to completion.

    Args:
        settings: Settings instance; if None, uses get_settings().
        client: httpx.AsyncClient for making requests. If None, creates one
            internally. If provided, the caller is responsible for closing it.

    Yields:
        Employee models for each active employee in the directory.

    Raises:
        AuthError: On 401/403.
        TransientError: On 5xx or transport errors after retries.
    """
    if settings is None:
        settings = get_settings()

    async def _fetch_paginated(http_client: httpx.AsyncClient) -> AsyncIterator[Employee]:
        """Inner generator that performs the paginated fetch using the provided client."""
        # Build bearer token auth header
        headers = {"Authorization": f"Bearer {settings.hr_api_token}"}

        cursor: str | None = None
        while True:
            # Build request params
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor

            # Make request with retry logic
            response = await request_with_retry(
                http_client,
                "hr",
                "GET",
                f"{settings.hr_base_url}/employees",
                params=params if params else None,
                headers=headers,
            )

            data = response.json()

            # Yield each active employee
            for employee_payload in data.get("data", []):
                if employee_payload.get("is_active"):
                    employee = Employee(**employee_payload)
                    yield employee

            # Check pagination
            next_cursor = data.get("next_cursor")
            if not next_cursor:
                break

            cursor = next_cursor

    if client is not None:
        # Use provided client (caller is responsible for cleanup)
        async for employee in _fetch_paginated(client):
            yield employee
    else:
        # Create and manage our own client
        async with httpx.AsyncClient() as own_client:
            async for employee in _fetch_paginated(own_client):
                yield employee
