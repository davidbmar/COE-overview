"""Tests for shared retry+error helper using tenacity+httpx."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
import respx

from coe.ingest.base import request_with_retry
from coe.ingest.errors import AuthError, IngestError, TransientError

pytestmark = pytest.mark.unit


class TestRequestWithRetry:
    """Tests for request_with_retry helper."""

    @pytest.mark.asyncio
    async def test_success_returns_response(self) -> None:
        """A 200 response is returned immediately."""
        async with respx.mock:
            route = respx.get("https://api.example.com/data").mock(
                return_value=httpx.Response(200, json={"result": "ok"})
            )

            async with httpx.AsyncClient() as client:
                response = await request_with_retry(
                    client, "test", "GET", "https://api.example.com/data"
                )

            assert response.status_code == 200
            assert route.called

    @pytest.mark.asyncio
    async def test_2xx_success(self) -> None:
        """2xx responses are treated as success."""
        async with respx.mock:
            respx.post("https://api.example.com/data").mock(
                return_value=httpx.Response(201, json={"id": "123"})
            )

            async with httpx.AsyncClient() as client:
                response = await request_with_retry(
                    client, "test", "POST", "https://api.example.com/data"
                )

            assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_401_raises_auth_error_no_retries(self) -> None:
        """A 401 raises AuthError on first call with no retries."""
        async with respx.mock:
            route = respx.get("https://api.example.com/data").mock(
                return_value=httpx.Response(401, text="Unauthorized")
            )

            async with httpx.AsyncClient() as client:
                with pytest.raises(AuthError) as exc_info:
                    await request_with_retry(client, "jira", "GET", "https://api.example.com/data")

            assert exc_info.value.source == "jira"
            assert "401" in exc_info.value.message
            # Verify no retries: should be called exactly once
            assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_403_raises_auth_error_no_retries(self) -> None:
        """A 403 raises AuthError on first call with no retries."""
        async with respx.mock:
            route = respx.get("https://api.example.com/data").mock(
                return_value=httpx.Response(403, text="Forbidden")
            )

            async with httpx.AsyncClient() as client:
                with pytest.raises(AuthError) as exc_info:
                    await request_with_retry(client, "wiz", "GET", "https://api.example.com/data")

            assert exc_info.value.source == "wiz"
            assert "403" in exc_info.value.message
            assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_other_4xx_raises_ingest_error(self) -> None:
        """Other 4xx (400, 404) raises IngestError (not retriable)."""
        async with respx.mock:
            route = respx.get("https://api.example.com/data").mock(
                return_value=httpx.Response(404, text="Not found")
            )

            async with httpx.AsyncClient() as client:
                with pytest.raises(IngestError) as exc_info:
                    await request_with_retry(client, "test", "GET", "https://api.example.com/data")

            assert exc_info.value.source == "test"
            assert "404" in exc_info.value.message
            # No retries for non-retriable 4xx
            assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_500_retries_then_fails(self) -> None:
        """Repeated 5xx up to max_retries raises TransientError."""
        async with respx.mock:
            route = respx.get("https://api.example.com/data").mock(
                return_value=httpx.Response(500, text="Server error")
            )

            async with httpx.AsyncClient() as client:
                with pytest.raises(TransientError) as exc_info:
                    await request_with_retry(
                        client,
                        "test",
                        "GET",
                        "https://api.example.com/data",
                        max_retries=2,
                    )

            error = exc_info.value
            assert error.source == "test"
            assert error.last_status == 500
            # max_retries=2 means up to 2 attempts (initial + 1 retry), but tenacity
            # counts total attempts; we expect initial + 2 retries = 3 attempts
            assert error.retries_attempted == 2
            # Should have been called 3 times: initial + 2 retries
            assert route.call_count == 3

    @pytest.mark.asyncio
    async def test_408_retries_then_fails(self) -> None:
        """408 (Request Timeout) is retriable; fails after max_retries."""
        async with respx.mock:
            respx.get("https://api.example.com/data").mock(
                return_value=httpx.Response(408, text="Request Timeout")
            )

            async with httpx.AsyncClient() as client:
                with pytest.raises(TransientError) as exc_info:
                    await request_with_retry(
                        client,
                        "test",
                        "GET",
                        "https://api.example.com/data",
                        max_retries=2,
                    )

            error = exc_info.value
            assert error.last_status == 408
            assert error.retries_attempted == 2

    @pytest.mark.asyncio
    async def test_429_retries_then_fails(self) -> None:
        """429 (Too Many Requests) is retriable; fails after max_retries."""
        async with respx.mock:
            respx.get("https://api.example.com/data").mock(
                return_value=httpx.Response(429, text="Too Many Requests")
            )

            async with httpx.AsyncClient() as client:
                with pytest.raises(TransientError) as exc_info:
                    await request_with_retry(
                        client,
                        "test",
                        "GET",
                        "https://api.example.com/data",
                        max_retries=1,
                    )

            error = exc_info.value
            assert error.last_status == 429
            assert error.retries_attempted == 1

    @pytest.mark.asyncio
    async def test_503_with_retry_after_succeeds(self) -> None:
        """5xx followed by success eventually returns the 200."""
        async with respx.mock:
            # First call: 503, second call: 200
            route = respx.get("https://api.example.com/data").mock(
                side_effect=[
                    httpx.Response(503, text="Service Unavailable"),
                    httpx.Response(200, json={"result": "ok"}),
                ]
            )

            async with httpx.AsyncClient() as client:
                response = await request_with_retry(
                    client, "test", "GET", "https://api.example.com/data"
                )

            assert response.status_code == 200
            assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_after_integer_seconds(self) -> None:
        """Retry-After header (integer seconds) is honored."""
        sleep_calls: list[float] = []

        async def mock_sleep(duration: float) -> None:
            sleep_calls.append(duration)

        async with respx.mock:
            respx.get("https://api.example.com/data").mock(
                side_effect=[
                    httpx.Response(429, text="Too Many Requests", headers={"Retry-After": "2"}),
                    httpx.Response(200, json={"result": "ok"}),
                ]
            )

            async with httpx.AsyncClient() as client:
                with patch("asyncio.sleep", side_effect=mock_sleep):
                    response = await request_with_retry(
                        client, "test", "GET", "https://api.example.com/data"
                    )

            assert response.status_code == 200
            # Verify that sleep was called with at least the Retry-After value
            assert len(sleep_calls) > 0
            assert sleep_calls[0] >= 2

    @pytest.mark.asyncio
    async def test_retry_after_takes_precedence_over_exponential(self) -> None:
        """Retry-After (when >= exponential backoff) is used instead of backoff."""
        sleep_calls: list[float] = []

        async def mock_sleep(duration: float) -> None:
            sleep_calls.append(duration)

        async with respx.mock:
            respx.get("https://api.example.com/data").mock(
                side_effect=[
                    httpx.Response(429, text="Too Many Requests", headers={"Retry-After": "10"}),
                    httpx.Response(200, json={"result": "ok"}),
                ]
            )

            async with httpx.AsyncClient() as client:
                with patch("asyncio.sleep", side_effect=mock_sleep):
                    response = await request_with_retry(
                        client, "test", "GET", "https://api.example.com/data"
                    )

            assert response.status_code == 200
            # Should sleep for at least 10 seconds (Retry-After)
            assert len(sleep_calls) > 0
            assert sleep_calls[0] >= 10

    @pytest.mark.asyncio
    async def test_transport_error_retries_then_fails(self) -> None:
        """Transport errors are retriable; fail with last_status=None after max_retries."""
        async with respx.mock:
            respx.get("https://api.example.com/data").mock(
                side_effect=httpx.ConnectError("Connection failed")
            )

            async with httpx.AsyncClient() as client:
                with pytest.raises(TransientError) as exc_info:
                    await request_with_retry(
                        client,
                        "test",
                        "GET",
                        "https://api.example.com/data",
                        max_retries=2,
                    )

            error = exc_info.value
            assert error.source == "test"
            assert error.last_status is None
            assert error.retries_attempted == 2

    @pytest.mark.asyncio
    async def test_transport_error_followed_by_success(self) -> None:
        """Transport error followed by success eventually returns the response."""
        async with respx.mock:
            route = respx.get("https://api.example.com/data").mock(
                side_effect=[
                    httpx.ConnectError("Connection failed"),
                    httpx.Response(200, json={"result": "ok"}),
                ]
            )

            async with httpx.AsyncClient() as client:
                response = await request_with_retry(
                    client, "test", "GET", "https://api.example.com/data"
                )

            assert response.status_code == 200
            assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_read_timeout_retries(self) -> None:
        """ReadTimeout errors are retriable."""
        async with respx.mock:
            respx.get("https://api.example.com/data").mock(
                side_effect=[
                    httpx.ReadTimeout("Read timed out"),
                    httpx.Response(200, json={"result": "ok"}),
                ]
            )

            async with httpx.AsyncClient() as client:
                response = await request_with_retry(
                    client, "test", "GET", "https://api.example.com/data"
                )

            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_post_with_json_body(self) -> None:
        """POST requests with json body work correctly."""
        async with respx.mock:
            route = respx.post("https://api.example.com/data").mock(
                return_value=httpx.Response(201, json={"id": "123"})
            )

            async with httpx.AsyncClient() as client:
                response = await request_with_retry(
                    client,
                    "test",
                    "POST",
                    "https://api.example.com/data",
                    json={"key": "value"},
                )

            assert response.status_code == 201
            assert route.called
            # Verify the request body was sent
            assert route.calls[0].request.content

    @pytest.mark.asyncio
    async def test_custom_headers(self) -> None:
        """Custom headers are passed through."""
        async with respx.mock:
            route = respx.get("https://api.example.com/data").mock(
                return_value=httpx.Response(200, json={"result": "ok"})
            )

            async with httpx.AsyncClient() as client:
                response = await request_with_retry(
                    client,
                    "test",
                    "GET",
                    "https://api.example.com/data",
                    headers={"Authorization": "Bearer token"},
                )

            assert response.status_code == 200
            # Verify header was sent
            assert route.calls[0].request.headers["Authorization"] == "Bearer token"

    @pytest.mark.asyncio
    async def test_params_are_passed(self) -> None:
        """Query params are passed through."""
        async with respx.mock:
            route = respx.get("https://api.example.com/data", params={"since": "2026-01-01"}).mock(
                return_value=httpx.Response(200, json={"result": "ok"})
            )

            async with httpx.AsyncClient() as client:
                response = await request_with_retry(
                    client,
                    "test",
                    "GET",
                    "https://api.example.com/data",
                    params={"since": "2026-01-01"},
                )

            assert response.status_code == 200
            assert route.called

    @pytest.mark.asyncio
    async def test_multiple_retries_with_different_statuses(self) -> None:
        """Sequence of 500, 503, 500, then 200 eventually succeeds."""
        async with respx.mock:
            route = respx.get("https://api.example.com/data").mock(
                side_effect=[
                    httpx.Response(500, text="Server error 1"),
                    httpx.Response(503, text="Service unavailable"),
                    httpx.Response(500, text="Server error 2"),
                    httpx.Response(200, json={"result": "ok"}),
                ]
            )

            async with httpx.AsyncClient() as client:
                response = await request_with_retry(
                    client, "test", "GET", "https://api.example.com/data", max_retries=5
                )

            assert response.status_code == 200
            assert route.call_count == 4

    @pytest.mark.asyncio
    async def test_jitter_produces_different_sleeps_for_same_sequence(self) -> None:
        """I1: Exponential backoff with jitter produces varying sleep times."""
        from coe.ingest.base import _compute_sleep_time

        # Same attempt_number should produce different sleep times due to jitter
        sleep_times = []
        for _ in range(10):
            sleep_time = _compute_sleep_time(None, attempt_number=2)
            sleep_times.append(sleep_time)

        # All should be in range [1, 1.5] (2^(2-1) = 2, with jitter up to 2*0.5=1)
        for sleep_time in sleep_times:
            assert 2.0 <= sleep_time <= 3.0  # 2^1 + [0, 1]

        # They should not all be identical (jitter working)
        assert len(set(sleep_times)) > 1, "Jitter should produce varying times"

    @pytest.mark.asyncio
    async def test_retry_after_http_date_format(self) -> None:
        """I2: Retry-After header in HTTP-date format is parsed and honored."""
        from datetime import timedelta
        from email.utils import formatdate

        sleep_calls: list[float] = []

        async def mock_sleep(duration: float) -> None:
            sleep_calls.append(duration)

        # Create a Retry-After header with a future timestamp (5 seconds from now)
        future_time = datetime.now(UTC) + timedelta(seconds=5)
        http_date = formatdate(timeval=future_time.timestamp(), localtime=False, usegmt=True)

        async with respx.mock:
            respx.get("https://api.example.com/data").mock(
                side_effect=[
                    httpx.Response(
                        429,
                        text="Too Many Requests",
                        headers={"Retry-After": http_date},
                    ),
                    httpx.Response(200, json={"result": "ok"}),
                ]
            )

            async with httpx.AsyncClient() as client:
                # Patch the sleep and the datetime to control time
                with patch("coe.ingest.base.asyncio.sleep", side_effect=mock_sleep):
                    response = await request_with_retry(
                        client, "test", "GET", "https://api.example.com/data"
                    )

            assert response.status_code == 200
            # Sleep should have been called with approximately 5 seconds
            assert len(sleep_calls) > 0
            # Allow some tolerance due to timing variations
            assert 4.5 <= sleep_calls[0] <= 5.5
