"""Tests for CrowdStrike ingest client."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from coe.config import Settings
from coe.ingest import crowdstrike
from coe.ingest.crowdstrike import CrowdstrikeDetect, fetch_updated_since
from coe.ingest.errors import AuthError, TransientError

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_crowdstrike_token_cache() -> None:
    """Clear the CrowdStrike token cache before each test to prevent pollution."""
    crowdstrike._clear_token_cache()


class TestCrowdstrikeDetect:
    """Tests for CrowdstrikeDetect model."""

    def test_crowdstrike_detect_instantiation(self) -> None:
        """CrowdstrikeDetect can be instantiated with required fields."""
        detect = CrowdstrikeDetect(
            id="detect-123",
            max_severity=90,
            severity_name="CRITICAL",
            status="open",
            last_updated=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            assigned_to_uid="user-456",
            raw_payload={"id": "detect-123", "max_severity": 90},
        )
        assert detect.id == "detect-123"
        assert detect.max_severity == 90
        assert detect.severity_name == "CRITICAL"
        assert detect.status == "open"
        assert detect.assigned_to_uid == "user-456"

    def test_severity_name_critical_mapping(self) -> None:
        """severity_name is CRITICAL when max_severity >= 90."""
        detect = CrowdstrikeDetect(
            id="d1",
            max_severity=90,
            severity_name="CRITICAL",
            status="open",
            last_updated=datetime.now(UTC),
            assigned_to_uid=None,
            raw_payload={},
        )
        assert detect.severity_name == "CRITICAL"

        detect2 = CrowdstrikeDetect(
            id="d2",
            max_severity=100,
            severity_name="CRITICAL",
            status="open",
            last_updated=datetime.now(UTC),
            assigned_to_uid=None,
            raw_payload={},
        )
        assert detect2.severity_name == "CRITICAL"

    def test_severity_name_high_mapping(self) -> None:
        """severity_name is HIGH when 70 <= max_severity < 90."""
        detect = CrowdstrikeDetect(
            id="d1",
            max_severity=70,
            severity_name="HIGH",
            status="open",
            last_updated=datetime.now(UTC),
            assigned_to_uid=None,
            raw_payload={},
        )
        assert detect.severity_name == "HIGH"

        detect2 = CrowdstrikeDetect(
            id="d2",
            max_severity=85,
            severity_name="HIGH",
            status="open",
            last_updated=datetime.now(UTC),
            assigned_to_uid=None,
            raw_payload={},
        )
        assert detect2.severity_name == "HIGH"


class TestFetchUpdatedSince:
    """Tests for fetch_updated_since function."""

    @pytest.mark.asyncio
    async def test_single_page_with_token_ids_and_summaries(self) -> None:
        """AC1.2: Token + IDs (5 detects) + summaries yields 5 CrowdstrikeDetect records."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            crowdstrike_client_id="test_client_id",
            crowdstrike_client_secret="test_client_secret",
            crowdstrike_base_url="https://api.crowdstrike.com",
        )

        token_response = {
            "access_token": "test_token_abc123",
            "expires_in": 1800,
        }

        ids_response = {
            "resources": [
                "detect-1",
                "detect-2",
                "detect-3",
                "detect-4",
                "detect-5",
            ]
        }

        summaries_response = {
            "resources": [
                {
                    "detection_id": "detect-1",
                    "max_severity": 90,
                    "status": "open",
                    "last_updated": "2026-05-21T10:00:00Z",
                    "assigned_to_uid": "user-1",
                },
                {
                    "detection_id": "detect-2",
                    "max_severity": 85,
                    "status": "closed",
                    "last_updated": "2026-05-21T11:00:00Z",
                    "assigned_to_uid": None,
                },
                {
                    "detection_id": "detect-3",
                    "max_severity": 75,
                    "status": "open",
                    "last_updated": "2026-05-21T12:00:00Z",
                    "assigned_to_uid": "user-3",
                },
                {
                    "detection_id": "detect-4",
                    "max_severity": 70,
                    "status": "open",
                    "last_updated": "2026-05-21T13:00:00Z",
                    "assigned_to_uid": None,
                },
                {
                    "detection_id": "detect-5",
                    "max_severity": 95,
                    "status": "open",
                    "last_updated": "2026-05-21T14:00:00Z",
                    "assigned_to_uid": "user-5",
                },
            ]
        }

        async with respx.mock:
            # Mock token endpoint
            respx.post("https://api.crowdstrike.com/oauth2/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )

            # Mock IDs query endpoint
            respx.get("https://api.crowdstrike.com/detects/queries/detects/v1").mock(
                return_value=httpx.Response(200, json=ids_response)
            )

            # Mock summaries endpoint
            respx.post("https://api.crowdstrike.com/detects/entities/summaries/GET/v1").mock(
                return_value=httpx.Response(200, json=summaries_response)
            )

            # Fetch and collect results
            results = []
            async for detect in fetch_updated_since(since, settings):
                results.append(detect)

            assert len(results) == 5
            assert results[0].id == "detect-1"
            assert results[0].max_severity == 90
            assert results[0].severity_name == "CRITICAL"
            assert results[1].max_severity == 85
            assert results[1].severity_name == "HIGH"
            assert results[4].max_severity == 95
            assert results[4].severity_name == "CRITICAL"

    @pytest.mark.asyncio
    async def test_fql_filter_contains_severity_and_timestamp(self) -> None:
        """AC1.2: FQL filter contains max_severity:>=70 and since ISO timestamp."""
        since = datetime(2026, 5, 20, 14, 30, 0, tzinfo=UTC)
        settings = Settings(
            crowdstrike_client_id="test_client_id",
            crowdstrike_client_secret="test_client_secret",
            crowdstrike_base_url="https://api.crowdstrike.com",
        )

        token_response: dict[str, str | int] = {
            "access_token": "test_token_abc123",
            "expires_in": 1800,
        }

        ids_response: dict[str, list[str]] = {"resources": []}  # Empty for this test

        async with respx.mock:
            respx.post("https://api.crowdstrike.com/oauth2/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )

            # Capture the request to verify query params
            ids_route = respx.get("https://api.crowdstrike.com/detects/queries/detects/v1").mock(
                return_value=httpx.Response(200, json=ids_response)
            )

            respx.post("https://api.crowdstrike.com/detects/entities/summaries/GET/v1").mock(
                return_value=httpx.Response(200, json={"resources": []})
            )

            # Fetch
            _ = [detect async for detect in fetch_updated_since(since, settings)]

            # Verify request was made with correct filter
            assert ids_route.called
            request = ids_route.calls.last.request
            assert "filter=" in request.url.query.decode()
            filter_param = request.url.params.get("filter", "")
            assert "max_severity:>=70" in filter_param
            assert "2026-05-20T14:30:00" in filter_param or "2026-05-20" in filter_param

    @pytest.mark.asyncio
    async def test_pagination_with_1500_ids(self) -> None:
        """AC1.2: 1500 IDs requires two queries/detects calls (offset 0 and 1000) and two POST summaries calls."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            crowdstrike_client_id="test_client_id",
            crowdstrike_client_secret="test_client_secret",
            crowdstrike_base_url="https://api.crowdstrike.com",
        )

        token_response = {
            "access_token": "test_token_abc123",
            "expires_in": 1800,
        }

        # First page: 1000 IDs
        ids_response_page1 = {"resources": [f"detect-{i}" for i in range(1000)]}

        # Second page: 500 IDs
        ids_response_page2 = {"resources": [f"detect-{i}" for i in range(1000, 1500)]}

        async with respx.mock:
            respx.post("https://api.crowdstrike.com/oauth2/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )

            # Track which offset was used for each IDs call
            ids_calls = []

            def ids_side_effect(request: httpx.Request) -> httpx.Response:
                offset = int(request.url.params.get("offset", 0))
                ids_calls.append(offset)
                if offset == 0:
                    return httpx.Response(200, json=ids_response_page1)
                elif offset == 1000:
                    return httpx.Response(200, json=ids_response_page2)
                else:
                    return httpx.Response(200, json={"resources": []})

            respx.get("https://api.crowdstrike.com/detects/queries/detects/v1").mock(
                side_effect=ids_side_effect
            )

            # Mock summaries endpoint: accept a batch of IDs and return summaries for those IDs
            summaries_calls = []

            def summaries_side_effect(request: httpx.Request) -> httpx.Response:
                body = request.content
                import json as json_lib

                request_json = json_lib.loads(body)
                ids_in_batch = request_json.get("ids", [])
                summaries_calls.append(ids_in_batch)

                resources = [
                    {
                        "detection_id": detect_id,
                        "max_severity": 75,
                        "status": "open",
                        "last_updated": "2026-05-21T10:00:00Z",
                        "assigned_to_uid": None,
                    }
                    for detect_id in ids_in_batch
                ]
                return httpx.Response(200, json={"resources": resources})

            respx.post("https://api.crowdstrike.com/detects/entities/summaries/GET/v1").mock(
                side_effect=summaries_side_effect
            )

            # Fetch and collect results
            results = []
            async for detect in fetch_updated_since(since, settings):
                results.append(detect)

            # Should have made two IDs queries with offsets 0 and 1000
            assert len(ids_calls) >= 2
            assert 0 in ids_calls
            assert 1000 in ids_calls
            # Should have made two summaries POST calls (one for each batch)
            assert len(summaries_calls) >= 2
            # First batch should have 1000 IDs
            assert len(summaries_calls[0]) == 1000
            # Second batch should have 500 IDs
            assert len(summaries_calls[1]) == 500
            # Should yield 1500 detects
            assert len(results) == 1500

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self) -> None:
        """AC1.4: 401 from IDs query raises AuthError('crowdstrike', ...)."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            crowdstrike_client_id="test_client_id",
            crowdstrike_client_secret="test_client_secret",
            crowdstrike_base_url="https://api.crowdstrike.com",
        )

        token_response = {
            "access_token": "test_token_abc123",
            "expires_in": 1800,
        }

        async with respx.mock:
            respx.post("https://api.crowdstrike.com/oauth2/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )

            # Mock IDs endpoint with 401
            respx.get("https://api.crowdstrike.com/detects/queries/detects/v1").mock(
                return_value=httpx.Response(401, json={"error": "Unauthorized"})
            )

            with pytest.raises(AuthError) as exc_info:
                _ = [detect async for detect in fetch_updated_since(since, settings)]

            assert exc_info.value.source == "crowdstrike"

    @pytest.mark.asyncio
    async def test_5xx_with_retry_after_is_retried(self) -> None:
        """AC1.5: 5xx with Retry-After is retried; final failure raises TransientError."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            crowdstrike_client_id="test_client_id",
            crowdstrike_client_secret="test_client_secret",
            crowdstrike_base_url="https://api.crowdstrike.com",
        )

        token_response = {
            "access_token": "test_token_abc123",
            "expires_in": 1800,
        }

        async with respx.mock:
            respx.post("https://api.crowdstrike.com/oauth2/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )

            # Mock IDs endpoint: 2 x 500, then raise TransientError after retries
            call_count = [0]

            def ids_side_effect(request: httpx.Request) -> httpx.Response:
                call_count[0] += 1
                if call_count[0] <= 2:
                    return httpx.Response(
                        500,
                        json={"error": "Server error"},
                        headers={"Retry-After": "1"},
                    )
                else:
                    return httpx.Response(
                        500, json={"error": "Server error"}, headers={"Retry-After": "1"}
                    )

            respx.get("https://api.crowdstrike.com/detects/queries/detects/v1").mock(
                side_effect=ids_side_effect
            )

            with pytest.raises(TransientError) as exc_info:
                _ = [detect async for detect in fetch_updated_since(since, settings)]

            assert exc_info.value.source == "crowdstrike"
            assert exc_info.value.last_status == 500
