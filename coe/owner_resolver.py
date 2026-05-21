"""Owner resolver for mapping owner emails to manager emails via the employees table.

This module provides a pure resolver layer (OwnerResolver) that does case-insensitive
lookups in a pre-loaded employee dict, plus a thin DB-backed loader (load_resolver)
that populates the dict from the employees table.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coe.db.models import Employee as EmployeeORM


@dataclass(frozen=True)
class ResolvedOwner:
    """Result of owner resolution lookup."""

    owner_email: str | None
    manager_email: str | None
    missing_owner_in_hr: bool


class OwnerResolver:
    """Pure resolver layer for owner → manager email lookup.

    Accepts a pre-loaded dict of { owner_email: manager_email_or_None }
    and performs case-insensitive lookups.
    """

    def __init__(self, employees: Mapping[str, str | None]) -> None:
        """Initialize resolver with employee map.

        Args:
            employees: Mapping of { owner_email: manager_email_or_None }.
                      Keys are case-insensitive; stored lowercased internally.
        """
        self._table = {k.lower(): v for k, v in employees.items()}

    def resolve(self, owner_email: str | None) -> ResolvedOwner:
        """Resolve an owner email to manager email and HR status.

        Args:
            owner_email: Owner email to look up (or None).

        Returns:
            ResolvedOwner with manager_email set if found in HR,
            missing_owner_in_hr=True if owner_email is provided but not found.
        """
        if owner_email is None:
            return ResolvedOwner(None, None, missing_owner_in_hr=False)

        normalized = owner_email.lower()
        if normalized in self._table:
            return ResolvedOwner(
                owner_email=owner_email,
                manager_email=self._table[normalized],
                missing_owner_in_hr=False,
            )

        return ResolvedOwner(
            owner_email=owner_email,
            manager_email=None,
            missing_owner_in_hr=True,
        )


async def load_resolver(session: AsyncSession) -> OwnerResolver:
    """Load and return an OwnerResolver from the employees table.

    Pulls the entire employees table into memory once per pipeline run.

    Args:
        session: AsyncSession for querying the employees table.

    Returns:
        OwnerResolver pre-populated with all active employees.
    """
    rows = (await session.execute(select(EmployeeORM.email, EmployeeORM.manager_email))).all()
    return OwnerResolver({email: manager for email, manager in rows})
