"""Tests for internal HR ingest client."""

from __future__ import annotations

import httpx
import pytest
import respx

from coe.config import Settings
from coe.ingest.errors import AuthError, TransientError
from coe.ingest.hr import Employee, fetch_all_active_employees

pytestmark = pytest.mark.unit


class TestEmployee:
    """Tests for Employee model."""

    def test_employee_instantiation(self) -> None:
        """Employee can be instantiated with required fields."""
        emp = Employee(
            email="alice@example.com",
            manager_email="manager@example.com",
            org_path="Engineering/Security",
            is_active=True,
        )
        assert emp.email == "alice@example.com"
        assert emp.manager_email == "manager@example.com"
        assert emp.org_path == "Engineering/Security"
        assert emp.is_active is True

    def test_employee_with_none_manager(self) -> None:
        """Employee can have None manager_email."""
        emp = Employee(
            email="bob@example.com",
            manager_email=None,
            org_path="Executive",
            is_active=True,
        )
        assert emp.manager_email is None

    def test_employee_with_none_org_path(self) -> None:
        """Employee can have None org_path."""
        emp = Employee(
            email="carol@example.com",
            manager_email="manager@example.com",
            org_path=None,
            is_active=True,
        )
        assert emp.org_path is None


class TestFetchAllActiveEmployees:
    """Tests for fetch_all_active_employees function."""

    @pytest.mark.asyncio
    async def test_single_page_active_employees(self) -> None:
        """AC1.3: Single-page response with 3 active employees yields 3 Employee models."""
        settings = Settings(
            hr_base_url="https://hr.example.com",
            hr_api_token="test_token",
        )

        response_data = {
            "data": [
                {
                    "email": "alice@example.com",
                    "manager_email": "manager@example.com",
                    "org_path": "Engineering/Security",
                    "is_active": True,
                },
                {
                    "email": "bob@example.com",
                    "manager_email": "manager@example.com",
                    "org_path": "Engineering/Platform",
                    "is_active": True,
                },
                {
                    "email": "carol@example.com",
                    "manager_email": None,
                    "org_path": "Executive",
                    "is_active": True,
                },
            ],
            "next_cursor": None,
        }

        with respx.mock:
            respx.get("https://hr.example.com/employees").mock(
                return_value=httpx.Response(200, json=response_data)
            )

            employees = []
            async for emp in fetch_all_active_employees(settings):
                employees.append(emp)

            assert len(employees) == 3
            assert employees[0].email == "alice@example.com"
            assert employees[0].manager_email == "manager@example.com"
            assert employees[0].org_path == "Engineering/Security"
            assert employees[1].email == "bob@example.com"
            assert employees[2].email == "carol@example.com"
            assert employees[2].manager_email is None

    @pytest.mark.asyncio
    async def test_filters_inactive_employees(self) -> None:
        """AC1.3: Inactive employees in response are filtered out (3 active + 2 inactive → only 3 yielded)."""
        settings = Settings(
            hr_base_url="https://hr.example.com",
            hr_api_token="test_token",
        )

        response_data = {
            "data": [
                {
                    "email": "alice@example.com",
                    "manager_email": "manager@example.com",
                    "org_path": "Engineering/Security",
                    "is_active": True,
                },
                {
                    "email": "inactive1@example.com",
                    "manager_email": "manager@example.com",
                    "org_path": "Engineering/Legacy",
                    "is_active": False,
                },
                {
                    "email": "bob@example.com",
                    "manager_email": "manager@example.com",
                    "org_path": "Engineering/Platform",
                    "is_active": True,
                },
                {
                    "email": "inactive2@example.com",
                    "manager_email": None,
                    "org_path": "Engineering/Deprecated",
                    "is_active": False,
                },
                {
                    "email": "carol@example.com",
                    "manager_email": None,
                    "org_path": "Executive",
                    "is_active": True,
                },
            ],
            "next_cursor": None,
        }

        with respx.mock:
            respx.get("https://hr.example.com/employees").mock(
                return_value=httpx.Response(200, json=response_data)
            )

            employees = []
            async for emp in fetch_all_active_employees(settings):
                employees.append(emp)

            assert len(employees) == 3
            assert employees[0].email == "alice@example.com"
            assert employees[1].email == "bob@example.com"
            assert employees[2].email == "carol@example.com"

    @pytest.mark.asyncio
    async def test_pagination_with_next_cursor(self) -> None:
        """AC1.3: Two-page response with next_cursor works (4 active employees split across pages → 4 yielded)."""
        settings = Settings(
            hr_base_url="https://hr.example.com",
            hr_api_token="test_token",
        )

        page1_data = {
            "data": [
                {
                    "email": "alice@example.com",
                    "manager_email": "manager@example.com",
                    "org_path": "Engineering/Security",
                    "is_active": True,
                },
                {
                    "email": "bob@example.com",
                    "manager_email": "manager@example.com",
                    "org_path": "Engineering/Platform",
                    "is_active": True,
                },
            ],
            "next_cursor": "cursor_page_2",
        }

        page2_data = {
            "data": [
                {
                    "email": "carol@example.com",
                    "manager_email": "manager@example.com",
                    "org_path": "Engineering/Backend",
                    "is_active": True,
                },
                {
                    "email": "dave@example.com",
                    "manager_email": None,
                    "org_path": "Executive",
                    "is_active": True,
                },
            ],
            "next_cursor": None,
        }

        with respx.mock:
            # Mock the second call with cursor first (respx matches in reverse order)
            respx.get("https://hr.example.com/employees", params={"cursor": "cursor_page_2"}).mock(
                return_value=httpx.Response(200, json=page2_data)
            )
            # Mock the first call without cursor
            respx.get("https://hr.example.com/employees").mock(
                return_value=httpx.Response(200, json=page1_data)
            )

            employees = []
            async for emp in fetch_all_active_employees(settings):
                employees.append(emp)

            assert len(employees) == 4
            assert employees[0].email == "alice@example.com"
            assert employees[1].email == "bob@example.com"
            assert employees[2].email == "carol@example.com"
            assert employees[3].email == "dave@example.com"

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self) -> None:
        """AC1.4: 401 raises AuthError('hr', ...)."""
        settings = Settings(
            hr_base_url="https://hr.example.com",
            hr_api_token="invalid_token",
        )

        with respx.mock:
            respx.get("https://hr.example.com/employees").mock(return_value=httpx.Response(401))

            with pytest.raises(AuthError) as exc_info:
                async for _ in fetch_all_active_employees(settings):
                    pass

            assert exc_info.value.source == "hr"

    @pytest.mark.asyncio
    async def test_403_raises_auth_error(self) -> None:
        """AC1.4: 403 raises AuthError('hr', ...)."""
        settings = Settings(
            hr_base_url="https://hr.example.com",
            hr_api_token="test_token",
        )

        with respx.mock:
            respx.get("https://hr.example.com/employees").mock(return_value=httpx.Response(403))

            with pytest.raises(AuthError) as exc_info:
                async for _ in fetch_all_active_employees(settings):
                    pass

            assert exc_info.value.source == "hr"

    @pytest.mark.asyncio
    async def test_5xx_retried_then_succeeds(self) -> None:
        """AC1.5: 5xx retried; success on second attempt."""
        settings = Settings(
            hr_base_url="https://hr.example.com",
            hr_api_token="test_token",
        )

        success_data = {
            "data": [
                {
                    "email": "alice@example.com",
                    "manager_email": "manager@example.com",
                    "org_path": "Engineering/Security",
                    "is_active": True,
                }
            ],
            "next_cursor": None,
        }

        with respx.mock:
            respx.get("https://hr.example.com/employees").mock(
                side_effect=[
                    httpx.Response(503),
                    httpx.Response(200, json=success_data),
                ]
            )

            employees = []
            async for emp in fetch_all_active_employees(settings):
                employees.append(emp)

            assert len(employees) == 1
            assert employees[0].email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_5xx_max_retries_exhausted(self) -> None:
        """AC1.5: 5xx retried; final failure raises TransientError('hr', ..., last_status=500)."""
        settings = Settings(
            hr_base_url="https://hr.example.com",
            hr_api_token="test_token",
        )

        with respx.mock:
            # Respond with 503 every time to exhaust retries
            respx.get("https://hr.example.com/employees").mock(return_value=httpx.Response(503))

            with pytest.raises(TransientError) as exc_info:
                async for _ in fetch_all_active_employees(settings):
                    pass

            assert exc_info.value.source == "hr"
            assert exc_info.value.last_status == 503
