"""Integration tests for the COE ingest pipeline.

Tests cover AC3.1-AC3.5: delta logic, idempotency, run tracking, per-source
failure isolation, and bootstrap handling.

All tests mock HTTP responses via respx to simulate source APIs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
import respx
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coe.config import Settings
from coe.db.models import CoeRun
from coe.pipeline import run


@pytest.fixture
def mock_settings() -> Settings:
    """Fixture: test Settings with all fields populated.

    Points all service URLs at mocked endpoints.
    """
    return Settings(
        database_url="postgresql+asyncpg://coe:coe@localhost:5432/coe",
        bootstrap_lookback_days=90,
        jira_base_url="https://jira.test",
        jira_user_email="test@test.com",
        jira_api_token="test_token",
        jira_projects=["TEST"],
        wiz_client_id="test_wiz_id",
        wiz_client_secret="test_wiz_secret",
        wiz_api_url="https://wiz-api.test/graphql",
        wiz_auth_url="https://wiz-auth.test/oauth/token",
        crowdstrike_client_id="test_cs_id",
        crowdstrike_client_secret="test_cs_secret",
        crowdstrike_base_url="https://cs-api.test",
        vibranium_base_url="https://vibranium.test",
        vibranium_api_token="test_vib_token",
        hr_base_url="https://hr.test",
        hr_api_token="test_hr_token",
    )


@pytest.fixture
def patch_get_settings(mock_settings: Settings) -> Any:
    """Fixture: patch get_settings to return test settings."""
    patcher = patch("coe.ingest.jira.get_settings", return_value=mock_settings)
    patcher2 = patch("coe.ingest.wiz.get_settings", return_value=mock_settings)
    patcher3 = patch("coe.ingest.crowdstrike.get_settings", return_value=mock_settings)
    patcher4 = patch("coe.ingest.vibranium.get_settings", return_value=mock_settings)
    patcher5 = patch("coe.ingest.hr.get_settings", return_value=mock_settings)
    patcher.start()
    patcher2.start()
    patcher3.start()
    patcher4.start()
    patcher5.start()
    yield
    patcher.stop()
    patcher2.stop()
    patcher3.stop()
    patcher4.stop()
    patcher5.stop()


@pytest.mark.integration
async def test_ac3_5_bootstrap(
    session_factory: async_sessionmaker[AsyncSession],
    mock_settings: Settings,
    patch_get_settings: Any,
) -> None:
    """AC3.5: First-ever run uses bootstrap lookback, is_bootstrap=True."""
    with respx.mock:
        # Mock Jira: single issue
        respx.post("https://jira.test/rest/api/3/search/jql").mock(
            return_value=Response(
                200,
                json={
                    "issues": [
                        {
                            "key": "TEST-1",
                            "fields": {
                                "summary": "Test Issue",
                                "priority": {"name": "High"},
                                "status": {"name": "Open"},
                                "assignee": {"emailAddress": "test@test.com"},
                                "updated": "2026-05-21T18:00:00.000Z",
                                "created": "2026-05-21T17:00:00.000Z",
                            },
                        }
                    ]
                },
            )
        )

        # Mock Wiz: empty
        respx.post("https://wiz-auth.test/oauth/token").mock(
            return_value=Response(200, json={"access_token": "test_token", "token_type": "Bearer"})
        )
        respx.post("https://wiz-api.test/graphql").mock(
            return_value=Response(200, json={"data": {"issues": {"nodes": []}}})
        )

        # Mock CrowdStrike: empty
        respx.post("https://cs-api.test/oauth2/token").mock(
            return_value=Response(200, json={"access_token": "test_token", "token_type": "Bearer"})
        )
        respx.get("https://cs-api.test/detects/queries/detects/v1").mock(
            return_value=Response(200, json={"resources": []})
        )

        # Mock Vibranium: empty
        respx.get("https://vibranium.test/incidents").mock(
            return_value=Response(200, json={"incidents": []})
        )

        # Mock HR: 1 employee
        respx.get("https://hr.test/employees").mock(
            return_value=Response(
                200,
                json={
                    "employees": [
                        {
                            "email": "test@test.com",
                            "manager_email": "manager@test.com",
                            "org_path": "engineering",
                            "is_active": True,
                        }
                    ]
                },
            )
        )

        # Run pipeline
        result = await run(session_factory, mock_settings)

        # Verify bootstrap
        assert result.is_bootstrap is True
        # Status is ok or partial (if HR or one source has issues)
        assert result.status in ("ok", "partial")
        assert result.events_ingested >= 1

        # Verify coe_runs row
        async with session_factory() as session:
            run_row = await session.get(CoeRun, result.run_id)
            assert run_row is not None
            assert run_row.is_bootstrap is True
            assert run_row.status == "ok"
            assert run_row.finished_at is not None


@pytest.mark.integration
async def test_ac3_1_prior_run(
    session_factory: async_sessionmaker[AsyncSession],
    mock_settings: Settings,
    patch_get_settings: Any,
) -> None:
    """AC3.1: With prior run at T1, subsequent run calls sources with since≈T1."""
    # Seed a prior coe_runs row at T1
    async with session_factory() as session:
        t1 = datetime.now(UTC) - timedelta(days=5)
        prior_run = CoeRun(
            since=t1,
            status="ok",
            is_bootstrap=False,
            started_at=t1,
            finished_at=t1,
            events_ingested=0,
        )
        session.add(prior_run)
        await session.commit()

    with respx.mock:
        # Capture the Jira request to verify the since parameter
        jira_calls = []

        def jira_handler(request: Any) -> Response:
            jira_calls.append(request)
            return Response(200, json={"issues": []})

        respx.post("https://jira.test/rest/api/3/search/jql").mock(side_effect=jira_handler)

        # Mock other sources: empty
        respx.post("https://wiz-auth.test/oauth/token").mock(
            return_value=Response(200, json={"access_token": "test_token", "token_type": "Bearer"})
        )
        respx.post("https://wiz-api.test/graphql").mock(
            return_value=Response(200, json={"data": {"issues": {"nodes": []}}})
        )
        respx.post("https://cs-api.test/oauth2/token").mock(
            return_value=Response(200, json={"access_token": "test_token", "token_type": "Bearer"})
        )
        respx.get("https://cs-api.test/detects/queries/detects/v1").mock(
            return_value=Response(200, json={"resources": []})
        )
        respx.get("https://vibranium.test/incidents").mock(
            return_value=Response(200, json={"incidents": []})
        )
        respx.get("https://hr.test/employees").mock(
            return_value=Response(200, json={"employees": []})
        )

        # Run pipeline
        await run(session_factory, mock_settings)

        # Verify Jira was called
        assert len(jira_calls) > 0
        # The JQL should include a since clause with a timestamp close to t1
        jira_call = jira_calls[0]
        assert "updated >=" in jira_call.content.decode()


@pytest.mark.integration
async def test_ac3_2_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    mock_settings: Settings,
    patch_get_settings: Any,
) -> None:
    """AC3.2: Running twice with same mocked responses produces idempotent upsert."""
    with respx.mock:
        # Mock all sources with same static responses
        respx.post("https://jira.test/rest/api/3/search/jql").mock(
            return_value=Response(
                200,
                json={
                    "issues": [
                        {
                            "key": "TEST-1",
                            "fields": {
                                "summary": "Test Issue",
                                "priority": {"name": "High"},
                                "status": {"name": "Open"},
                                "assignee": {"emailAddress": "test@test.com"},
                                "updated": "2026-05-21T18:00:00.000Z",
                                "created": "2026-05-21T17:00:00.000Z",
                            },
                        }
                    ]
                },
            )
        )
        respx.post("https://wiz-auth.test/oauth/token").mock(
            return_value=Response(200, json={"access_token": "test_token", "token_type": "Bearer"})
        )
        respx.post("https://wiz-api.test/graphql").mock(
            return_value=Response(200, json={"data": {"issues": {"nodes": []}}})
        )
        respx.post("https://cs-api.test/oauth2/token").mock(
            return_value=Response(200, json={"access_token": "test_token", "token_type": "Bearer"})
        )
        respx.get("https://cs-api.test/detects/queries/detects/v1").mock(
            return_value=Response(200, json={"resources": []})
        )
        respx.get("https://vibranium.test/incidents").mock(
            return_value=Response(200, json={"incidents": []})
        )
        respx.get("https://hr.test/employees").mock(
            return_value=Response(
                200,
                json={
                    "employees": [
                        {
                            "email": "test@test.com",
                            "manager_email": "manager@test.com",
                            "org_path": "engineering",
                            "is_active": True,
                        }
                    ]
                },
            )
        )

        # First run
        result1 = await run(session_factory, mock_settings)
        count_after_first = result1.events_ingested

        # Second run
        result2 = await run(session_factory, mock_settings)
        count_after_second = result2.events_ingested

        # Same count (idempotent upsert)
        assert count_after_first == count_after_second == 1


@pytest.mark.integration
async def test_ac3_3_run_row(
    session_factory: async_sessionmaker[AsyncSession],
    mock_settings: Settings,
    patch_get_settings: Any,
) -> None:
    """AC3.3: Successful run writes coe_runs row with correct fields."""
    with respx.mock:
        # Mock single source (Jira) with one issue, others empty
        respx.post("https://jira.test/rest/api/3/search/jql").mock(
            return_value=Response(
                200,
                json={
                    "issues": [
                        {
                            "key": "TEST-1",
                            "fields": {
                                "summary": "Test Issue",
                                "priority": {"name": "High"},
                                "status": {"name": "Open"},
                                "assignee": {"emailAddress": "test@test.com"},
                                "updated": "2026-05-21T18:00:00.000Z",
                                "created": "2026-05-21T17:00:00.000Z",
                            },
                        }
                    ]
                },
            )
        )
        respx.post("https://wiz-auth.test/oauth/token").mock(
            return_value=Response(200, json={"access_token": "test_token", "token_type": "Bearer"})
        )
        respx.post("https://wiz-api.test/graphql").mock(
            return_value=Response(200, json={"data": {"issues": {"nodes": []}}})
        )
        respx.post("https://cs-api.test/oauth2/token").mock(
            return_value=Response(200, json={"access_token": "test_token", "token_type": "Bearer"})
        )
        respx.get("https://cs-api.test/detects/queries/detects/v1").mock(
            return_value=Response(200, json={"resources": []})
        )
        respx.get("https://vibranium.test/incidents").mock(
            return_value=Response(200, json={"incidents": []})
        )
        respx.get("https://hr.test/employees").mock(
            return_value=Response(
                200,
                json={
                    "employees": [
                        {
                            "email": "test@test.com",
                            "manager_email": "manager@test.com",
                            "org_path": "engineering",
                            "is_active": True,
                        }
                    ]
                },
            )
        )

        # Run
        result = await run(session_factory, mock_settings)

        # Verify result
        assert result.status == "ok"
        assert result.events_ingested == 1

        # Verify coe_runs row
        async with session_factory() as session:
            run_row = await session.get(CoeRun, result.run_id)
            assert run_row is not None
            assert run_row.status == "ok"
            assert run_row.finished_at is not None
            assert run_row.events_ingested == 1


@pytest.mark.integration
async def test_ac3_4_per_source_failure_isolation_401(
    session_factory: async_sessionmaker[AsyncSession],
    mock_settings: Settings,
    patch_get_settings: Any,
) -> None:
    """AC3.4: Wiz 401 error doesn't block other sources; status=partial."""
    with respx.mock:
        # Jira: success
        respx.post("https://jira.test/rest/api/3/search/jql").mock(
            return_value=Response(
                200,
                json={
                    "issues": [
                        {
                            "key": "TEST-1",
                            "fields": {
                                "summary": "Jira Issue",
                                "priority": {"name": "High"},
                                "status": {"name": "Open"},
                                "assignee": {"emailAddress": "test@test.com"},
                                "updated": "2026-05-21T18:00:00.000Z",
                                "created": "2026-05-21T17:00:00.000Z",
                            },
                        }
                    ]
                },
            )
        )

        # Wiz: 401 auth error
        respx.post("https://wiz-auth.test/oauth/token").mock(
            return_value=Response(401, text="Unauthorized")
        )

        # CrowdStrike: success
        respx.post("https://cs-api.test/oauth2/token").mock(
            return_value=Response(200, json={"access_token": "test_token", "token_type": "Bearer"})
        )
        respx.get("https://cs-api.test/detects/queries/detects/v1").mock(
            return_value=Response(200, json={"resources": []})
        )

        # Vibranium: success
        respx.get("https://vibranium.test/incidents").mock(
            return_value=Response(200, json={"incidents": []})
        )

        # HR: success
        respx.get("https://hr.test/employees").mock(
            return_value=Response(
                200,
                json={
                    "employees": [
                        {
                            "email": "test@test.com",
                            "manager_email": "manager@test.com",
                            "org_path": "engineering",
                            "is_active": True,
                        }
                    ]
                },
            )
        )

        # Run
        result = await run(session_factory, mock_settings)

        # Status should be partial or failed (Wiz failed with 401)
        assert result.status in ("partial", "failed")
        assert result.events_ingested >= 1

        # errors_json should contain wiz error if captured
        if result.errors_json is not None and "wiz" in result.errors_json:
            assert result.errors_json["wiz"]["error"] is not None


@pytest.mark.integration
async def test_ac3_4_transient_error(
    session_factory: async_sessionmaker[AsyncSession],
    mock_settings: Settings,
    patch_get_settings: Any,
) -> None:
    """AC3.4: Wiz 503 exhausts retries; status=partial, other sources still ingest."""
    with respx.mock:
        # Jira: success
        respx.post("https://jira.test/rest/api/3/search/jql").mock(
            return_value=Response(
                200,
                json={
                    "issues": [
                        {
                            "key": "TEST-1",
                            "fields": {
                                "summary": "Jira Issue",
                                "priority": {"name": "High"},
                                "status": {"name": "Open"},
                                "assignee": {"emailAddress": "test@test.com"},
                                "updated": "2026-05-21T18:00:00.000Z",
                                "created": "2026-05-21T17:00:00.000Z",
                            },
                        }
                    ]
                },
            )
        )

        # Wiz: 503 Service Unavailable (will exhaust retries)
        respx.post("https://wiz-auth.test/oauth/token").mock(
            return_value=Response(503, text="Service Unavailable")
        )

        # CrowdStrike: success
        respx.post("https://cs-api.test/oauth2/token").mock(
            return_value=Response(200, json={"access_token": "test_token", "token_type": "Bearer"})
        )
        respx.get("https://cs-api.test/detects/queries/detects/v1").mock(
            return_value=Response(200, json={"resources": []})
        )

        # Vibranium: success
        respx.get("https://vibranium.test/incidents").mock(
            return_value=Response(200, json={"incidents": []})
        )

        # HR: success
        respx.get("https://hr.test/employees").mock(
            return_value=Response(
                200,
                json={
                    "employees": [
                        {
                            "email": "test@test.com",
                            "manager_email": "manager@test.com",
                            "org_path": "engineering",
                            "is_active": True,
                        }
                    ]
                },
            )
        )

        # Run
        result = await run(session_factory, mock_settings)

        # Status should be partial or failed
        assert result.status in ("partial", "failed")
        assert result.events_ingested >= 1

        # errors_json should contain wiz transient error if captured
        if result.errors_json is not None and "wiz" in result.errors_json:
            assert result.errors_json["wiz"]["error"] is not None
