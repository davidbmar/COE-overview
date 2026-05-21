"""Tests for Wiz GraphQL ingest client."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from coe.config import Settings
from coe.ingest import wiz
from coe.ingest.errors import AuthError, TransientError
from coe.ingest.wiz import WizIssue, fetch_updated_since

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_wiz_token_cache() -> None:
    """Clear the Wiz token cache before each test to prevent pollution."""
    wiz._clear_token_cache()


class TestWizIssue:
    """Tests for WizIssue model."""

    def test_wiz_issue_instantiation(self) -> None:
        """WizIssue can be instantiated with required fields."""
        issue = WizIssue(
            id="issue-123",
            severity="HIGH",
            status="OPEN",
            entity_name="app-server",
            assignee_email="alice@example.com",
            updated_at=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            created_at=datetime(2026, 5, 20, 10, 0, 0, tzinfo=UTC),
            raw_payload={"id": "issue-123", "severity": "HIGH"},
        )
        assert issue.id == "issue-123"
        assert issue.severity == "HIGH"
        assert issue.status == "OPEN"
        assert issue.entity_name == "app-server"
        assert issue.assignee_email == "alice@example.com"


class TestFetchUpdatedSince:
    """Tests for fetch_updated_since function."""

    @pytest.mark.asyncio
    async def test_single_page_with_token_and_issues(self) -> None:
        """AC1.2: Single-page response with token + issues yields WizIssue objects."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            wiz_client_id="test_client_id",
            wiz_client_secret="test_client_secret",
            wiz_api_url="https://api.wiz.io/graphql",
            wiz_auth_url="https://auth.wiz.io/oauth/token",
        )

        token_response = {
            "access_token": "test_token_abc123",
            "expires_in": 1800,
        }

        graphql_response = {
            "data": {
                "issues": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "issue-1",
                            "severity": "CRITICAL",
                            "status": "OPEN",
                            "updatedAt": "2026-05-21T10:00:00Z",
                            "createdAt": "2026-05-20T10:00:00Z",
                            "entitySnapshot": {"name": "app-server"},
                            "projects": [{"id": "proj-1", "name": "Project A"}],
                            "assignee": {"email": "alice@example.com"},
                        },
                        {
                            "id": "issue-2",
                            "severity": "HIGH",
                            "status": "RESOLVED",
                            "updatedAt": "2026-05-21T11:00:00Z",
                            "createdAt": "2026-05-20T11:00:00Z",
                            "entitySnapshot": {"name": "database"},
                            "projects": [{"id": "proj-2", "name": "Project B"}],
                            "assignee": None,
                        },
                    ],
                }
            }
        }

        async with respx.mock:
            # Mock token endpoint
            respx.post("https://auth.wiz.io/oauth/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )
            # Mock GraphQL endpoint
            respx.post("https://api.wiz.io/graphql").mock(
                return_value=httpx.Response(200, json=graphql_response)
            )

            issues = []
            async for issue in fetch_updated_since(since, settings=settings):
                issues.append(issue)

            assert len(issues) == 2
            assert issues[0].id == "issue-1"
            assert issues[0].severity == "CRITICAL"
            assert issues[0].status == "OPEN"
            assert issues[0].entity_name == "app-server"
            assert issues[0].assignee_email == "alice@example.com"
            assert issues[1].id == "issue-2"
            assert issues[1].severity == "HIGH"
            assert issues[1].assignee_email is None

    @pytest.mark.asyncio
    async def test_graphql_request_has_correct_severity_filter(self) -> None:
        """AC1.2: GraphQL request body contains HIGH/CRITICAL severity filter."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            wiz_client_id="test_client_id",
            wiz_client_secret="test_client_secret",
            wiz_api_url="https://api.wiz.io/graphql",
            wiz_auth_url="https://auth.wiz.io/oauth/token",
        )

        token_response = {"access_token": "test_token_abc123", "expires_in": 1800}
        graphql_response = {
            "data": {
                "issues": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [],
                }
            }
        }

        async with respx.mock:
            respx.post("https://auth.wiz.io/oauth/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )
            graphql_route = respx.post("https://api.wiz.io/graphql").mock(
                return_value=httpx.Response(200, json=graphql_response)
            )

            async for _ in fetch_updated_since(since, settings=settings):
                pass

            # Check the GraphQL request body
            assert graphql_route.called
            request = graphql_route.calls[0].request
            body = json.loads(request.content.decode())

            # Check severity filter
            assert "variables" in body
            assert "filter" in body["variables"]
            assert "severity" in body["variables"]["filter"]
            assert body["variables"]["filter"]["severity"] == ["HIGH", "CRITICAL"]

            # Check since timestamp is in updatedAt filter
            assert "updatedAt" in body["variables"]["filter"]
            assert "after" in body["variables"]["filter"]["updatedAt"]
            assert "2026-05-20" in body["variables"]["filter"]["updatedAt"]["after"]

    @pytest.mark.asyncio
    async def test_token_endpoint_has_client_credentials_grant(self) -> None:
        """AC1.2: Token request includes grant_type=client_credentials."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            wiz_client_id="test_client_id",
            wiz_client_secret="test_client_secret",
            wiz_api_url="https://api.wiz.io/graphql",
            wiz_auth_url="https://auth.wiz.io/oauth/token",
        )

        token_response = {"access_token": "test_token_abc123", "expires_in": 1800}
        graphql_response = {
            "data": {
                "issues": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [],
                }
            }
        }

        async with respx.mock:
            token_route = respx.post("https://auth.wiz.io/oauth/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )
            respx.post("https://api.wiz.io/graphql").mock(
                return_value=httpx.Response(200, json=graphql_response)
            )

            async for _ in fetch_updated_since(since, settings=settings):
                pass

            # Check token request body
            assert token_route.called
            request = token_route.calls[0].request
            body = request.content.decode()

            # Should be form-encoded
            assert "grant_type=client_credentials" in body
            assert "client_id=test_client_id" in body
            assert "client_secret=test_client_secret" in body
            assert "audience=wiz-api" in body

    @pytest.mark.asyncio
    async def test_two_page_pagination_via_end_cursor(self) -> None:
        """AC1.2: Two-page response paginated via endCursor."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            wiz_client_id="test_client_id",
            wiz_client_secret="test_client_secret",
            wiz_api_url="https://api.wiz.io/graphql",
            wiz_auth_url="https://auth.wiz.io/oauth/token",
        )

        token_response = {"access_token": "test_token_abc123", "expires_in": 1800}

        page1_response = {
            "data": {
                "issues": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor_page2"},
                    "nodes": [
                        {
                            "id": "issue-1",
                            "severity": "HIGH",
                            "status": "OPEN",
                            "updatedAt": "2026-05-21T10:00:00Z",
                            "createdAt": "2026-05-20T10:00:00Z",
                            "entitySnapshot": {"name": "app-1"},
                            "projects": [{"id": "proj-1", "name": "Project A"}],
                            "assignee": {"email": "alice@example.com"},
                        }
                    ],
                }
            }
        }

        page2_response = {
            "data": {
                "issues": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "issue-2",
                            "severity": "CRITICAL",
                            "status": "RESOLVED",
                            "updatedAt": "2026-05-21T11:00:00Z",
                            "createdAt": "2026-05-20T11:00:00Z",
                            "entitySnapshot": {"name": "app-2"},
                            "projects": [{"id": "proj-2", "name": "Project B"}],
                            "assignee": None,
                        }
                    ],
                }
            }
        }

        async with respx.mock:
            respx.post("https://auth.wiz.io/oauth/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )
            graphql_route = respx.post("https://api.wiz.io/graphql")
            graphql_route.side_effect = [
                httpx.Response(200, json=page1_response),
                httpx.Response(200, json=page2_response),
            ]

            issues = []
            async for issue in fetch_updated_since(since, settings=settings):
                issues.append(issue)

            assert len(issues) == 2
            assert issues[0].id == "issue-1"
            assert issues[1].id == "issue-2"

            # Verify we made two GraphQL requests
            assert len(graphql_route.calls) == 2

            # Check that the second request has the endCursor
            second_body = json.loads(graphql_route.calls[1].request.content.decode())
            assert second_body["variables"]["after"] == "cursor_page2"

    @pytest.mark.asyncio
    async def test_token_cache_reused_across_calls(self) -> None:
        """AC1.2: Token is cached; two fetch_updated_since calls hit token endpoint only once."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            wiz_client_id="test_client_id",
            wiz_client_secret="test_client_secret",
            wiz_api_url="https://api.wiz.io/graphql",
            wiz_auth_url="https://auth.wiz.io/oauth/token",
        )

        token_response = {"access_token": "test_token_abc123", "expires_in": 1800}
        graphql_response = {
            "data": {
                "issues": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [],
                }
            }
        }

        async with respx.mock:
            token_route = respx.post("https://auth.wiz.io/oauth/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )
            respx.post("https://api.wiz.io/graphql").mock(
                return_value=httpx.Response(200, json=graphql_response)
            )

            # Make two fetch_updated_since calls
            async for _ in fetch_updated_since(since, settings=settings):
                pass
            async for _ in fetch_updated_since(since, settings=settings):
                pass

            # Token endpoint should have been called only once
            assert token_route.call_count == 1

    @pytest.mark.asyncio
    async def test_401_from_graphql_raises_auth_error(self) -> None:
        """AC1.4: 401 from GraphQL endpoint raises AuthError."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            wiz_client_id="test_client_id",
            wiz_client_secret="test_client_secret",
            wiz_api_url="https://api.wiz.io/graphql",
            wiz_auth_url="https://auth.wiz.io/oauth/token",
        )

        token_response = {"access_token": "test_token_abc123", "expires_in": 1800}

        async with respx.mock:
            respx.post("https://auth.wiz.io/oauth/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )
            graphql_route = respx.post("https://api.wiz.io/graphql").mock(
                return_value=httpx.Response(401, text="Unauthorized")
            )

            with pytest.raises(AuthError) as exc_info:
                async for _ in fetch_updated_since(since, settings=settings):
                    pass

            assert exc_info.value.source == "wiz"
            assert "401" in exc_info.value.message
            # M1: Verify no retries (call_count == 1)
            assert graphql_route.call_count == 1

    @pytest.mark.asyncio
    async def test_5xx_retried_then_succeeds(self) -> None:
        """AC1.5: 5xx from GraphQL is retried and succeeds."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            wiz_client_id="test_client_id",
            wiz_client_secret="test_client_secret",
            wiz_api_url="https://api.wiz.io/graphql",
            wiz_auth_url="https://auth.wiz.io/oauth/token",
        )

        token_response = {"access_token": "test_token_abc123", "expires_in": 1800}
        graphql_response = {
            "data": {
                "issues": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "issue-1",
                            "severity": "HIGH",
                            "status": "OPEN",
                            "updatedAt": "2026-05-21T10:00:00Z",
                            "createdAt": "2026-05-20T10:00:00Z",
                            "entitySnapshot": {"name": "app-1"},
                            "projects": [{"id": "proj-1", "name": "Project A"}],
                            "assignee": {"email": "alice@example.com"},
                        }
                    ],
                }
            }
        }

        async with respx.mock:
            respx.post("https://auth.wiz.io/oauth/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )
            graphql_route = respx.post("https://api.wiz.io/graphql")
            graphql_route.side_effect = [
                httpx.Response(503, text="Service Unavailable"),
                httpx.Response(503, text="Service Unavailable"),
                httpx.Response(200, json=graphql_response),
            ]

            issues = []
            async for issue in fetch_updated_since(since, settings=settings):
                issues.append(issue)

            assert len(issues) == 1
            assert issues[0].id == "issue-1"
            # Verify we made 3 GraphQL requests (2 failures, 1 success)
            assert len(graphql_route.calls) == 3

    @pytest.mark.asyncio
    async def test_5xx_all_retries_exhausted_raises_transient_error(self) -> None:
        """AC1.5: 5xx after max retries raises TransientError."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            wiz_client_id="test_client_id",
            wiz_client_secret="test_client_secret",
            wiz_api_url="https://api.wiz.io/graphql",
            wiz_auth_url="https://auth.wiz.io/oauth/token",
        )

        token_response = {"access_token": "test_token_abc123", "expires_in": 1800}

        async with respx.mock:
            respx.post("https://auth.wiz.io/oauth/token").mock(
                return_value=httpx.Response(200, json=token_response)
            )
            graphql_route = respx.post("https://api.wiz.io/graphql")
            # Return 5xx for all attempts (initial + 5 retries = 6 total)
            graphql_route.side_effect = [httpx.Response(503, text="Service Unavailable")] * 6

            with pytest.raises(TransientError) as exc_info:
                async for _ in fetch_updated_since(since, settings=settings):
                    pass

            assert exc_info.value.source == "wiz"
            assert exc_info.value.last_status == 503
            assert exc_info.value.retries_attempted == 5
