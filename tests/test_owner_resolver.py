"""Unit tests for coe.owner_resolver module (pure resolver, no DB).

Verifies AC2.3, AC2.4 behavior without database dependency.
"""

from __future__ import annotations

import pytest

from coe.owner_resolver import OwnerResolver, ResolvedOwner

pytestmark = pytest.mark.unit


class TestOwnerResolver:
    """Tests for OwnerResolver (pure layer)."""

    def test_resolve_case_insensitive_lookup_ac2_3(self) -> None:
        """AC2.3: resolver finds email case-insensitively, returns manager email."""
        resolver = OwnerResolver({"alice@x.com": "manager@x.com"})
        result = resolver.resolve("Alice@X.com")

        assert result.owner_email == "Alice@X.com"
        assert result.manager_email == "manager@x.com"
        assert result.missing_owner_in_hr is False

    def test_resolve_unknown_owner_ac2_4(self) -> None:
        """AC2.4: unknown owner_email returns None manager, missing_owner_in_hr=True."""
        resolver = OwnerResolver({"alice@x.com": "manager@x.com"})
        result = resolver.resolve("unknown@x.com")

        assert result.owner_email == "unknown@x.com"
        assert result.manager_email is None
        assert result.missing_owner_in_hr is True

    def test_resolve_none_owner(self) -> None:
        """None owner_email returns all-None with missing_owner_in_hr=False."""
        resolver = OwnerResolver({"alice@x.com": "manager@x.com"})
        result = resolver.resolve(None)

        assert result.owner_email is None
        assert result.manager_email is None
        assert result.missing_owner_in_hr is False

    def test_resolve_owner_with_none_manager(self) -> None:
        """Owner in table with manager_email=None → returns manager_email=None, missing_owner_in_hr=False."""
        resolver = OwnerResolver({"alice@x.com": None})
        result = resolver.resolve("alice@x.com")

        assert result.owner_email == "alice@x.com"
        assert result.manager_email is None
        assert result.missing_owner_in_hr is False

    def test_resolved_owner_is_frozen(self) -> None:
        """ResolvedOwner is a frozen dataclass."""
        result = ResolvedOwner(
            owner_email="test@x.com",
            manager_email="mgr@x.com",
            missing_owner_in_hr=False,
        )
        with pytest.raises(AttributeError):
            result.owner_email = "modified@x.com"  # type: ignore[misc]
