from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class CoeSeverity(enum.StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class Source(enum.StrEnum):
    JIRA = "jira"
    WIZ = "wiz"
    CROWDSTRIKE = "crowdstrike"
    VIBRANIUM = "vibranium"


class CoeEvent(Base):
    __tablename__ = "coe_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[Source] = mapped_column(
        Enum(Source, name="source_enum", values_callable=lambda x: [e.value for e in x]),
        index=True,
    )
    source_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(Text)
    severity: Mapped[CoeSeverity] = mapped_column(
        Enum(CoeSeverity, name="coe_severity", values_callable=lambda x: [e.value for e in x]),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(64))
    owner_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    manager_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    missing_owner_in_hr: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(32), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    coe_review_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_coe_events_source_sourceid"),
    )


class CoeRun(Base):
    __tablename__ = "coe_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # "ok" | "partial" | "failed"
    events_ingested: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_bootstrap: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    errors_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class Employee(Base):
    __tablename__ = "employees"

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    manager_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    org_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class _RawMixin:
    """Base mixin for per-source raw audit tables."""

    source_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), primary_key=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class JiraRaw(_RawMixin, Base):
    __tablename__ = "jira_raw"


class WizRaw(_RawMixin, Base):
    __tablename__ = "wiz_raw"


class CrowdstrikeRaw(_RawMixin, Base):
    __tablename__ = "crowdstrike_raw"


class VibraniumRaw(_RawMixin, Base):
    __tablename__ = "vibranium_raw"
