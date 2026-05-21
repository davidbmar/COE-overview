"""Tests for Jira ingest client."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from coe.config import Settings
from coe.ingest.errors import AuthError
from coe.ingest.jira import JiraIssue, fetch_updated_since

pytestmark = pytest.mark.unit


class TestJiraIssue:
    """Tests for JiraIssue model."""

    def test_jira_issue_instantiation(self) -> None:
        """JiraIssue can be instantiated with required fields."""
        issue = JiraIssue(
            key="SEC-123",
            summary="Fix security bug",
            priority="High",
            status="In Progress",
            assignee_email="alice@example.com",
            updated=datetime(2026, 5, 21, 10, 0, 0, tzinfo=UTC),
            created=datetime(2026, 5, 20, 10, 0, 0, tzinfo=UTC),
            raw_payload={
                "key": "SEC-123",
                "fields": {
                    "summary": "Fix security bug",
                    "priority": {"name": "High"},
                    "status": {"name": "In Progress"},
                    "assignee": {"emailAddress": "alice@example.com"},
                    "updated": "2026-05-21T10:00:00.000+0000",
                    "created": "2026-05-20T10:00:00.000+0000",
                },
            },
        )
        assert issue.key == "SEC-123"
        assert issue.summary == "Fix security bug"
        assert issue.priority == "High"
        assert issue.status == "In Progress"
        assert issue.assignee_email == "alice@example.com"


class TestFetchUpdatedSince:
    """Tests for fetch_updated_since function."""

    @pytest.mark.asyncio
    async def test_single_page_response(self) -> None:
        """AC1.1: Single-page response yields all issues."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            jira_base_url="https://capsule.atlassian.net",
            jira_user_email="test@capsule.com",
            jira_api_token="test_token",
            jira_projects=["SEC", "OPS"],
        )

        response_data = {
            "issues": [
                {
                    "key": "SEC-123",
                    "fields": {
                        "summary": "Fix bug",
                        "priority": {"name": "High"},
                        "status": {"name": "Done"},
                        "assignee": {"emailAddress": "alice@example.com"},
                        "updated": "2026-05-21T10:00:00.000+0000",
                        "created": "2026-05-20T10:00:00.000+0000",
                    },
                },
                {
                    "key": "OPS-456",
                    "fields": {
                        "summary": "Deploy service",
                        "priority": {"name": "Critical"},
                        "status": {"name": "In Progress"},
                        "assignee": {"emailAddress": "bob@example.com"},
                        "updated": "2026-05-21T11:00:00.000+0000",
                        "created": "2026-05-20T11:00:00.000+0000",
                    },
                },
                {
                    "key": "SEC-789",
                    "fields": {
                        "summary": "Review policy",
                        "priority": {"name": "Medium"},
                        "status": {"name": "Backlog"},
                        "assignee": None,
                        "updated": "2026-05-21T12:00:00.000+0000",
                        "created": "2026-05-20T12:00:00.000+0000",
                    },
                },
            ],
            "isLast": True,
        }

        async with respx.mock:
            route = respx.post("https://capsule.atlassian.net/rest/api/3/search/jql").mock(
                return_value=httpx.Response(200, json=response_data)
            )

            issues = []
            async for issue in fetch_updated_since(since, settings=settings):
                issues.append(issue)

            assert len(issues) == 3
            assert issues[0].key == "SEC-123"
            assert issues[0].summary == "Fix bug"
            assert issues[0].priority == "High"
            assert issues[1].key == "OPS-456"
            assert issues[1].assignee_email == "bob@example.com"
            # AC1.1: Check assignee_email is None when assignee is null
            assert issues[2].assignee_email is None

            # Verify request was made
            assert route.called
            # Verify the JQL in the request body includes project filter and since timestamp
            request = route.calls[0].request
            assert request.method == "POST"
            body = request.content.decode()
            assert "project IN" in body
            assert "SEC" in body
            assert "OPS" in body
            assert "updated >=" in body

    @pytest.mark.asyncio
    async def test_two_page_pagination(self) -> None:
        """AC1.1: Two-page response with nextPageToken is paginated correctly."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            jira_base_url="https://capsule.atlassian.net",
            jira_user_email="test@capsule.com",
            jira_api_token="test_token",
            jira_projects=["SEC"],
        )

        page1_data = {
            "issues": [
                {
                    "key": "SEC-1",
                    "fields": {
                        "summary": "Issue 1",
                        "priority": {"name": "High"},
                        "status": {"name": "Done"},
                        "assignee": {"emailAddress": "alice@example.com"},
                        "updated": "2026-05-21T10:00:00.000+0000",
                        "created": "2026-05-20T10:00:00.000+0000",
                    },
                }
            ],
            "isLast": False,
            "nextPageToken": "page2token",
        }

        page2_data = {
            "issues": [
                {
                    "key": "SEC-2",
                    "fields": {
                        "summary": "Issue 2",
                        "priority": {"name": "Medium"},
                        "status": {"name": "In Progress"},
                        "assignee": {"emailAddress": "bob@example.com"},
                        "updated": "2026-05-21T11:00:00.000+0000",
                        "created": "2026-05-20T11:00:00.000+0000",
                    },
                }
            ],
            "isLast": True,
        }

        async with respx.mock:
            route = respx.post("https://capsule.atlassian.net/rest/api/3/search/jql").mock(
                return_value=httpx.Response(200, json=page1_data)
            )
            # The second call should get page 2
            route.side_effect = [
                httpx.Response(200, json=page1_data),
                httpx.Response(200, json=page2_data),
            ]

            issues = []
            async for issue in fetch_updated_since(since, settings=settings):
                issues.append(issue)

            assert len(issues) == 2
            assert issues[0].key == "SEC-1"
            assert issues[1].key == "SEC-2"

            # Verify we made two requests
            assert len(route.calls) == 2
            # Verify second request contains the nextPageToken
            second_request = route.calls[1].request
            body = second_request.content.decode()
            assert "page2token" in body

    @pytest.mark.asyncio
    async def test_jira_projects_in_jql(self) -> None:
        """AC1.1: jira_projects allowlist appears in JQL."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            jira_base_url="https://capsule.atlassian.net",
            jira_user_email="test@capsule.com",
            jira_api_token="test_token",
            jira_projects=["PROJ1", "PROJ2", "PROJ3"],
        )

        response_data = {"issues": [], "isLast": True}

        async with respx.mock:
            route = respx.post("https://capsule.atlassian.net/rest/api/3/search/jql").mock(
                return_value=httpx.Response(200, json=response_data)
            )

            async for _ in fetch_updated_since(since, settings=settings):
                pass

            # Verify JQL contains all projects
            request = route.calls[0].request
            body = request.content.decode()
            assert "PROJ1" in body
            assert "PROJ2" in body
            assert "PROJ3" in body
            # The JQL is in JSON, so quotes are escaped
            assert "project IN" in body

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self) -> None:
        """AC1.4: 401 response raises AuthError."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            jira_base_url="https://capsule.atlassian.net",
            jira_user_email="test@capsule.com",
            jira_api_token="bad_token",
            jira_projects=["SEC"],
        )

        async with respx.mock:
            respx.post("https://capsule.atlassian.net/rest/api/3/search/jql").mock(
                return_value=httpx.Response(401, text="Unauthorized")
            )

            with pytest.raises(AuthError) as exc_info:
                async for _ in fetch_updated_since(since, settings=settings):
                    pass

            assert exc_info.value.source == "jira"
            assert "401" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_503_retry_succeeds(self) -> None:
        """AC1.5: 503 response is retried and succeeds."""
        since = datetime(2026, 5, 20, 0, 0, 0, tzinfo=UTC)
        settings = Settings(
            jira_base_url="https://capsule.atlassian.net",
            jira_user_email="test@capsule.com",
            jira_api_token="test_token",
            jira_projects=["SEC"],
        )

        response_data = {
            "issues": [
                {
                    "key": "SEC-1",
                    "fields": {
                        "summary": "Issue",
                        "priority": {"name": "High"},
                        "status": {"name": "Done"},
                        "assignee": {"emailAddress": "alice@example.com"},
                        "updated": "2026-05-21T10:00:00.000+0000",
                        "created": "2026-05-20T10:00:00.000+0000",
                    },
                }
            ],
            "isLast": True,
        }

        async with respx.mock:
            route = respx.post("https://capsule.atlassian.net/rest/api/3/search/jql")
            route.side_effect = [
                httpx.Response(503, text="Service Unavailable"),
                httpx.Response(503, text="Service Unavailable"),
                httpx.Response(200, json=response_data),
            ]

            issues = []
            async for issue in fetch_updated_since(since, settings=settings):
                issues.append(issue)

            assert len(issues) == 1
            assert issues[0].key == "SEC-1"
            # Verify we made 3 requests (2 failures, 1 success)
            assert len(route.calls) == 3
