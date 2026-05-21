"""Tests for Vibranium ingest client."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from coe.config import Settings
from coe.ingest.errors import AuthError, TransientError
from coe.ingest.vibranium import VibraniumIncident, fetch_updated_since

pytestmark = pytest.mark.unit


class TestVibraniumIncident:
    """Tests for VibraniumIncident model."""

    def test_vibranium_incident_instantiation(self) -> None:
        """VibraniumIncident can be instantiated with required fields."""
        incident = VibraniumIncident(
            id="incident-123",
            severity="critical",
            status="open",
            assignee_email="alice@example.com",
            updated_at=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            created_at=datetime(2026, 5, 20, 10, 0, 0, tzinfo=UTC),
            raw_payload={"id": "incident-123", "severity": "critical"},
        )
        assert incident.id == "incident-123"
        assert incident.severity == "critical"
        assert incident.status == "open"
        assert incident.assignee_email == "alice@example.com"

    def test_severity_is_raw_string(self) -> None:
        """severity field stores raw string value from Vibranium (not Python Enum)."""
        incident = VibraniumIncident(
            id="i1",
            severity="high",
            status="open",
            assignee_email=None,
            updated_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            raw_payload={},
        )
        # Verify it's a string, not an enum
        assert isinstance(incident.severity, str)
        assert incident.severity == "high"


class TestFetchUpdatedSince:
    """Tests for fetch_updated_since function."""

    @pytest.mark.asyncio
    async def test_single_page_incidents(self) -> None:
        """AC1.2: Single-page response yields VibraniumIncident objects with expected fields."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            vibranium_base_url="https://internal.example.com/vibranium",
            vibranium_api_token="test_token_xyz",
        )

        api_response = {
            "data": [
                {
                    "id": "incident-1",
                    "severity": "critical",
                    "status": "open",
                    "assignee_email": "alice@example.com",
                    "updated_at": "2026-05-21T10:00:00Z",
                    "created_at": "2026-05-20T10:00:00Z",
                },
                {
                    "id": "incident-2",
                    "severity": "high",
                    "status": "resolved",
                    "assignee_email": None,
                    "updated_at": "2026-05-21T11:00:00Z",
                    "created_at": "2026-05-20T11:00:00Z",
                },
            ],
            "next_cursor": None,
        }

        async with respx.mock:
            respx.get("https://internal.example.com/vibranium/incidents").mock(
                return_value=httpx.Response(200, json=api_response)
            )

            # Fetch and collect results
            results = []
            async for incident in fetch_updated_since(since, settings):
                results.append(incident)

            assert len(results) == 2
            assert results[0].id == "incident-1"
            assert results[0].severity == "critical"
            assert results[0].status == "open"
            assert results[0].assignee_email == "alice@example.com"
            assert results[1].id == "incident-2"
            assert results[1].severity == "high"

    @pytest.mark.asyncio
    async def test_cursor_pagination_across_pages(self) -> None:
        """AC1.2: Cursor pagination iterates across pages while next_cursor is non-null."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            vibranium_base_url="https://internal.example.com/vibranium",
            vibranium_api_token="test_token_xyz",
        )

        page1_response = {
            "data": [
                {
                    "id": "incident-1",
                    "severity": "critical",
                    "status": "open",
                    "assignee_email": None,
                    "updated_at": "2026-05-21T10:00:00Z",
                    "created_at": "2026-05-20T10:00:00Z",
                },
            ],
            "next_cursor": "cursor_page2",
        }

        page2_response = {
            "data": [
                {
                    "id": "incident-2",
                    "severity": "high",
                    "status": "open",
                    "assignee_email": "bob@example.com",
                    "updated_at": "2026-05-21T11:00:00Z",
                    "created_at": "2026-05-20T11:00:00Z",
                },
            ],
            "next_cursor": None,
        }

        async with respx.mock:
            # Track requests to verify pagination
            requests = []

            def side_effect(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                cursor = request.url.params.get("cursor")
                if cursor is None:
                    return httpx.Response(200, json=page1_response)
                elif cursor == "cursor_page2":
                    return httpx.Response(200, json=page2_response)
                else:
                    return httpx.Response(200, json={"data": [], "next_cursor": None})

            respx.get("https://internal.example.com/vibranium/incidents").mock(
                side_effect=side_effect
            )

            # Fetch and collect results
            results = []
            async for incident in fetch_updated_since(since, settings):
                results.append(incident)

            # Verify pagination occurred
            assert len(requests) >= 2
            # First request should have no cursor
            assert requests[0].url.params.get("cursor") is None
            # Second request should have cursor from first response
            assert requests[1].url.params.get("cursor") == "cursor_page2"
            # Should have collected 2 total incidents
            assert len(results) == 2

    @pytest.mark.asyncio
    async def test_query_params_include_severity_filter(self) -> None:
        """AC1.2: Query includes severity=high,critical and since timestamp."""
        since = datetime(2026, 5, 20, 14, 30, 0, tzinfo=UTC)
        settings = Settings(
            vibranium_base_url="https://internal.example.com/vibranium",
            vibranium_api_token="test_token_xyz",
        )

        api_response: dict[str, Any] = {"data": [], "next_cursor": None}

        async with respx.mock:
            request_route = respx.get("https://internal.example.com/vibranium/incidents").mock(
                return_value=httpx.Response(200, json=api_response)
            )

            _ = [incident async for incident in fetch_updated_since(since, settings)]

            # Verify request was made with correct query params
            assert request_route.called
            request = request_route.calls.last.request
            params = request.url.params
            assert params.get("severity") == "high,critical"
            # Since should appear in query
            assert "2026-05-20" in params.get("since", "")

    @pytest.mark.asyncio
    async def test_authorization_header_present(self) -> None:
        """Authorization header is sent as Bearer token."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            vibranium_base_url="https://internal.example.com/vibranium",
            vibranium_api_token="secret_token_123",
        )

        api_response: dict[str, Any] = {"data": [], "next_cursor": None}

        async with respx.mock:
            request_route = respx.get("https://internal.example.com/vibranium/incidents").mock(
                return_value=httpx.Response(200, json=api_response)
            )

            _ = [incident async for incident in fetch_updated_since(since, settings)]

            assert request_route.called
            request = request_route.calls.last.request
            auth_header = request.headers.get("authorization", "")
            assert auth_header == "Bearer secret_token_123"

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self) -> None:
        """AC1.4: 401 from incidents endpoint raises AuthError('vibranium', ...)."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            vibranium_base_url="https://internal.example.com/vibranium",
            vibranium_api_token="invalid_token",
        )

        async with respx.mock:
            route = respx.get("https://internal.example.com/vibranium/incidents").mock(
                return_value=httpx.Response(401, json={"error": "Unauthorized"})
            )

            with pytest.raises(AuthError) as exc_info:
                _ = [incident async for incident in fetch_updated_since(since, settings)]

            assert exc_info.value.source == "vibranium"
            # M1: Verify no retries (call_count == 1)
            assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_403_raises_auth_error(self) -> None:
        """AC1.4: 403 from incidents endpoint raises AuthError('vibranium', ...)."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            vibranium_base_url="https://internal.example.com/vibranium",
            vibranium_api_token="forbidden_token",
        )

        async with respx.mock:
            route = respx.get("https://internal.example.com/vibranium/incidents").mock(
                return_value=httpx.Response(403, json={"error": "Forbidden"})
            )

            with pytest.raises(AuthError) as exc_info:
                _ = [incident async for incident in fetch_updated_since(since, settings)]

            assert exc_info.value.source == "vibranium"
            # M1: Verify no retries (call_count == 1)
            assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_5xx_retried_and_final_failure_raises_transient_error(self) -> None:
        """AC1.5: 5xx is retried; final failure raises TransientError."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            vibranium_base_url="https://internal.example.com/vibranium",
            vibranium_api_token="test_token_xyz",
        )

        call_count = [0]

        def side_effect(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            # Always return 500 to exhaust retries
            return httpx.Response(500, json={"error": "Internal server error"})

        async with respx.mock:
            respx.get("https://internal.example.com/vibranium/incidents").mock(
                side_effect=side_effect
            )

            with pytest.raises(TransientError) as exc_info:
                _ = [incident async for incident in fetch_updated_since(since, settings)]

            assert exc_info.value.source == "vibranium"
            assert exc_info.value.last_status == 500
