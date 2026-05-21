"""Shared HTTP retry helper with structured error translation."""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from coe.ingest.errors import AuthError, IngestError, TransientError


async def request_with_retry(
    client: httpx.AsyncClient,
    source: str,
    method: str,
    url: str,
    *,
    max_retries: int = 5,
    json: Any | None = None,
    data: Any | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Make an HTTP request with retry logic, translating errors to structured types.

    Args:
        client: httpx.AsyncClient to use for the request.
        source: Source identifier (e.g., 'jira', 'wiz') for error context.
        method: HTTP method (GET, POST, etc.).
        url: Request URL.
        max_retries: Maximum number of retries for transient errors (default 5).
        json: JSON body for the request.
        data: Form-encoded body for the request.
        params: Query parameters for the request.
        headers: Custom headers for the request.

    Returns:
        httpx.Response on success (2xx).

    Raises:
        AuthError: On 401 or 403 (no retry).
        IngestError: On other 4xx errors (no retry).
        TransientError: On 408/429/5xx or transport errors after max_retries.
    """
    last_status: int | None = None
    retries_attempted = 0

    # Attempt loop: initial + up to max_retries retries
    for attempt_num in range(1, max_retries + 2):
        try:
            response = await client.request(
                method, url, json=json, data=data, params=params, headers=headers
            )

            # 2xx: success
            if 200 <= response.status_code < 300:
                return response

            # 401/403: auth error (no retry)
            if response.status_code in (401, 403):
                raise AuthError(source, f"HTTP {response.status_code}")

            # 408/429/5xx: retriable
            if response.status_code in (408, 429) or response.status_code >= 500:
                last_status = response.status_code
                retries_attempted = attempt_num - 1
                # Check if we have retries left
                if retries_attempted >= max_retries:
                    raise TransientError(
                        source,
                        f"HTTP {response.status_code}",
                        last_status=last_status,
                        retries_attempted=retries_attempted,
                    )
                # Sleep before retry
                sleep_time = _compute_sleep_time(response, attempt_num)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                continue

            # Other 4xx: client error (no retry)
            raise IngestError(source, f"HTTP {response.status_code}")

        except (httpx.TransportError, httpx.ConnectError, httpx.ReadTimeout) as e:
            # Transport errors are retriable
            retries_attempted = attempt_num - 1
            if retries_attempted >= max_retries:
                raise TransientError(
                    source,
                    f"Transport error: {type(e).__name__}",
                    last_status=None,
                    retries_attempted=retries_attempted,
                ) from e
            # Sleep before retry
            sleep_time = _compute_sleep_time(None, attempt_num)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            continue

        except (AuthError, IngestError):
            # Non-retriable errors; raise immediately
            raise

    # Should not reach here
    raise TransientError(
        source, "Max retries exceeded", last_status=last_status, retries_attempted=retries_attempted
    )


def _compute_sleep_time(response: httpx.Response | None, attempt_number: int) -> float:
    """Compute sleep time: max(Retry-After header, exponential backoff + jitter).

    Implements exponential backoff with jitter to prevent thundering herd.
    Respects Retry-After header (as integer seconds or HTTP-date format).

    Args:
        response: The HTTP response (None for transport errors).
        attempt_number: Current attempt number (1-based).

    Returns:
        Sleep duration in seconds.
    """
    # Extract Retry-After header if present
    retry_after_seconds = 0.0
    if response is not None:
        retry_after_header = response.headers.get("Retry-After")
        if retry_after_header:
            # Try parsing as integer seconds first
            try:
                retry_after_seconds = float(retry_after_header)
            except ValueError:
                # Try parsing as HTTP-date format
                try:
                    target = parsedate_to_datetime(retry_after_header)
                    if target is None:
                        retry_after_seconds = 0.0
                    else:
                        if target.tzinfo is None:
                            target = target.replace(tzinfo=UTC)
                        retry_after_seconds = max(0.0, (target - datetime.now(UTC)).total_seconds())
                except (TypeError, ValueError):
                    retry_after_seconds = 0.0

    # Exponential backoff: 2^(attempt_number - 1) with max of 60s
    base_delay = min(2 ** (attempt_number - 1), 60)

    # Add jitter: uniform random between 0 and base_delay * 0.5
    jitter = random.uniform(0, base_delay * 0.5)
    exponential_backoff = base_delay + jitter

    # Return max of Retry-After and exponential backoff
    return float(max(retry_after_seconds, exponential_backoff))
