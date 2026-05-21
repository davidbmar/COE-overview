# COE Overview — Phase 1: Project Scaffolding + DB Schema

**Goal:** Stand up the Python project (uv + ruff + mypy + pytest), the Postgres
schema (Alembic + the 7 tables + `coe_severity` enum), and a local
docker-compose for development.

**Architecture:** uv manages dependencies. SQLAlchemy 2.0 async declarative
models live in `coe/db/models.py` and are the source of truth for Alembic
autogenerate. Postgres 16 runs in docker-compose for local dev. All tooling
config sits in `pyproject.toml`.

**Tech Stack:** Python 3.11+, uv 0.11+, SQLAlchemy 2.0 + asyncpg, Alembic
1.18+ (async template), Ruff 0.15+, mypy 1.11+ (strict), pytest 8.2+ with
pytest-asyncio 0.24+, docker-compose, Postgres 16.

**Scope:** Phase 1 of 7 — infrastructure only. No business logic, no source
clients, no normalization.

**Codebase verified:** 2026-05-21 — greenfield. Only
`docs/design-plans/2026-05-21-coe-overview.md` and the implementation-plans
directory exist. No prior Python project, no `CLAUDE.md` / `AGENTS.md`, no
existing patterns to follow. Git repo on branch `coe-overview` tracking
`origin/coe-overview`.

---

## Acceptance Criteria Coverage

**Verifies: None.** Per the design plan's AC5, this phase is infrastructure
only. Operational verification:

- `uv sync` completes without errors.
- `alembic upgrade head` applies cleanly against a fresh Postgres 16.
- `pytest` collects (the smoke test passes; other phases will add real tests).
- `ruff check` and `mypy` pass on the scaffold.
- `docker compose up postgres` produces a healthy container.

---

<!-- START_SUBCOMPONENT_A (tasks 1-3) -->

<!-- START_TASK_1 -->
### Task 1: Initialize uv project with pyproject.toml

**Files:**
- Create: `/Users/dmar/src/COE-overview/pyproject.toml`
- Create: `/Users/dmar/src/COE-overview/.python-version`
- Create: `/Users/dmar/src/COE-overview/.gitignore`
- Create: `/Users/dmar/src/COE-overview/coe/__init__.py`
- Create: `/Users/dmar/src/COE-overview/tests/__init__.py`

**Step 1: Verify `uv` is installed**

```bash
uv --version
```
Expected: `uv 0.11.x` or later. If not installed: `brew install uv` (macOS).

**Step 2: Create `.python-version`**

```
3.11
```

**Step 3: Create `pyproject.toml`**

```toml
[project]
name = "coe-overview"
version = "0.1.0"
description = "Weekly Correction-of-Errors prep aggregator"
requires-python = ">=3.11"
dependencies = [
    "sqlalchemy[asyncio]>=2.0.30",
    "asyncpg>=0.29.0",
    "alembic>=1.18.0",
    "httpx>=0.28.0",
    "tenacity>=8.5.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.3.0",
    "structlog>=24.1.0",
]

[dependency-groups]
dev = [
    "ruff>=0.15.13",
    "mypy>=1.11.0",
    "pytest>=8.2.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=5.0.0",
    "respx>=0.21.0",
    "testcontainers[postgres]>=4.7.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["coe"]
```

**Step 4: Create `.gitignore`**

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
dist/
build/

# Tooling
.mypy_cache/
.ruff_cache/
.pytest_cache/
.coverage
htmlcov/

# Env
.env
.env.local

# OS
.DS_Store
```

**Step 5: Create the package and test directories**

```bash
mkdir -p /Users/dmar/src/COE-overview/coe /Users/dmar/src/COE-overview/tests
touch /Users/dmar/src/COE-overview/coe/__init__.py
touch /Users/dmar/src/COE-overview/tests/__init__.py
```

**Step 6: Sync dependencies**

```bash
cd /Users/dmar/src/COE-overview
uv sync
```
Expected: `Resolved N packages`, `Audited N packages`, no errors. Creates
`uv.lock` and `.venv/`.

**Step 7: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore coe/ tests/
git commit -m "chore: initialize uv project with core deps"
```
<!-- END_TASK_1 -->

<!-- START_TASK_2 -->
### Task 2: Add ruff / mypy / pytest config to pyproject.toml

**Files:**
- Modify: `/Users/dmar/src/COE-overview/pyproject.toml` (append `[tool.*]`
  sections)

**Step 1: Append tooling config to `pyproject.toml`**

Append the following sections to the end of `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py311"
line-length = 100
extend-exclude = [".venv", "alembic/versions"]

[tool.ruff.lint]
extend-select = [
    "E",    # pycodestyle errors
    "F",    # pyflakes
    "W",    # pycodestyle warnings
    "B",    # flake8-bugbear
    "UP",   # pyupgrade
    "I",    # isort
    "N",    # pep8-naming
    "RUF",  # ruff-specific
    "SIM",  # flake8-simplify
]
ignore = ["E501"]  # handled by formatter

[tool.ruff.format]
preview = true

[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = ["testcontainers.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "-v --strict-markers"
testpaths = ["tests"]
python_files = ["test_*.py"]
markers = [
    "unit: fast unit tests with no external dependencies",
    "integration: tests that hit a real Postgres or other external system",
]
```

**Step 2: Verify ruff and mypy pass on the empty scaffold**

```bash
cd /Users/dmar/src/COE-overview
uv run ruff check .
uv run ruff format --check .
uv run mypy coe tests
uv run pytest --collect-only
```
Expected: all four commands exit 0. `pytest --collect-only` reports
"collected 0 items" — that is fine for an empty scaffold.

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add ruff, mypy, pytest configuration"
```
<!-- END_TASK_2 -->

<!-- START_TASK_3 -->
### Task 3: Add pre-commit hooks

**Files:**
- Create: `/Users/dmar/src/COE-overview/.pre-commit-config.yaml`

**Step 1: Create `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.13
    hooks:
      - id: ruff-format
      - id: ruff
        args: ["--fix"]

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-added-large-files
        args: ["--maxkb=500"]

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.11.0
    hooks:
      - id: mypy
        additional_dependencies: ["pydantic", "sqlalchemy[mypy]"]
        args: ["--ignore-missing-imports"]
        exclude: "^alembic/versions/"
```

**Step 2: Install hooks locally** (one-time)

```bash
cd /Users/dmar/src/COE-overview
uv run --with pre-commit pre-commit install
uv run --with pre-commit pre-commit run --all-files
```
Expected: hooks install; first run may auto-fix trailing whitespace / EOF
issues. Re-stage any auto-fixes and continue.

**Step 3: Commit**

```bash
git add .pre-commit-config.yaml
git commit -m "chore: add pre-commit hooks"
```
<!-- END_TASK_3 -->

<!-- END_SUBCOMPONENT_A -->

<!-- START_SUBCOMPONENT_B (tasks 4-5) -->

<!-- START_TASK_4 -->
### Task 4: Local Postgres via docker-compose

**Files:**
- Create: `/Users/dmar/src/COE-overview/docker-compose.yml`
- Create: `/Users/dmar/src/COE-overview/.env.example`

**Step 1: Create `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16
    container_name: coe-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: coe
      POSTGRES_PASSWORD: coe
      POSTGRES_DB: coe
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U coe -d coe"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s

volumes:
  pgdata:
    driver: local
```

**Step 2: Create `.env.example`**

```
# Local dev defaults — copy to .env and override as needed.
DATABASE_URL=postgresql+asyncpg://coe:coe@localhost:5432/coe
```

**Step 3: Verify the container starts healthy**

```bash
cd /Users/dmar/src/COE-overview
docker compose up -d postgres
# Wait for healthy; this may take ~10s
for i in 1 2 3 4 5 6 7 8 9 10; do
  status=$(docker inspect -f '{{.State.Health.Status}}' coe-postgres 2>/dev/null)
  [ "$status" = "healthy" ] && break
  sleep 3
done
docker inspect -f '{{.State.Health.Status}}' coe-postgres
```
Expected final output: `healthy`.

**Step 4: Verify connectivity**

```bash
docker exec coe-postgres psql -U coe -d coe -c "SELECT version();"
```
Expected: prints the Postgres 16.x version banner.

**Step 5: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "chore: add docker-compose for local postgres"
```
<!-- END_TASK_4 -->

<!-- START_TASK_5 -->
### Task 5: Initialize Alembic with async env.py

**Files:**
- Create: `/Users/dmar/src/COE-overview/alembic.ini`
- Create: `/Users/dmar/src/COE-overview/alembic/env.py`
- Create: `/Users/dmar/src/COE-overview/alembic/script.py.mako`
- Create: `/Users/dmar/src/COE-overview/alembic/versions/.gitkeep`
- Create: `/Users/dmar/src/COE-overview/coe/db/__init__.py`
- Create: `/Users/dmar/src/COE-overview/coe/db/base.py`
- Create: `/Users/dmar/src/COE-overview/coe/config.py`

**Step 1: Create `coe/config.py`**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://coe:coe@localhost:5432/coe"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

**Step 2: Create `coe/db/__init__.py`** (empty file).

**Step 3: Create `coe/db/base.py`** (declarative base + import surface for
Alembic autogenerate)

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all COE models."""
```

**Step 3b: Create an empty `coe/db/models.py` placeholder** so the
`env.py` import in Step 6 doesn't fail before Task 6 fills in the models:

```python
"""Models live here. Populated in Task 6."""
```

**Step 4: Create `alembic.ini`**

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
version_path_separator = os
sqlalchemy.url = postgresql+asyncpg://coe:coe@localhost:5432/coe

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

**Step 5: Create `alembic/script.py.mako`**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

**Step 6: Create `alembic/env.py` (async-friendly)**

```python
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

from coe.config import get_settings
from coe.db.base import Base
import coe.db.models  # noqa: F401  # ensures models register with Base.metadata

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = config.get_main_option("sqlalchemy.url")
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

**Step 7: Create `alembic/versions/.gitkeep`** (empty file — keeps the dir
in git before any migrations exist).

**Step 8: Smoke check that alembic loads**

```bash
cd /Users/dmar/src/COE-overview
docker compose up -d postgres
# Wait briefly for healthy
for i in 1 2 3 4 5; do
  [ "$(docker inspect -f '{{.State.Health.Status}}' coe-postgres 2>/dev/null)" = "healthy" ] && break
  sleep 2
done
uv run alembic current
```
Expected: exits 0 with no migrations applied (`current` returns nothing
yet). The import surface is the empty `coe/db/models.py` placeholder from
Step 3b — no `ImportError`. We'll exercise migrations in Task 6.

**Step 9: Commit**

```bash
git add alembic.ini alembic/ coe/config.py coe/db/__init__.py coe/db/base.py coe/db/models.py
git commit -m "chore: initialize alembic with async env.py"
```
<!-- END_TASK_5 -->

<!-- END_SUBCOMPONENT_B -->

<!-- START_SUBCOMPONENT_C (tasks 6-7) -->

<!-- START_TASK_6 -->
### Task 6: Define SQLAlchemy models + initial migration

**Files:**
- Create: `/Users/dmar/src/COE-overview/coe/db/models.py`
- Create: `/Users/dmar/src/COE-overview/alembic/versions/0001_initial_schema.py`

**Step 1: Create `coe/db/models.py`**

```python
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from coe.db.base import Base


class CoeSeverity(str, enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class Source(str, enum.Enum):
    JIRA = "jira"
    WIZ = "wiz"
    CROWDSTRIKE = "crowdstrike"
    VIBRANIUM = "vibranium"


class CoeEvent(Base):
    __tablename__ = "coe_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[Source] = mapped_column(Enum(Source, name="source_enum"), index=True)
    source_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(Text)
    severity: Mapped[CoeSeverity] = mapped_column(
        Enum(CoeSeverity, name="coe_severity"), index=True
    )
    status: Mapped[str] = mapped_column(String(64))
    owner_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    manager_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    missing_owner_in_hr: Mapped[bool] = mapped_column(Boolean, default=False)
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(32), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    coe_review_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_coe_events_source_sourceid"),
    )


class CoeRun(Base):
    __tablename__ = "coe_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # "ok" | "partial" | "failed"
    events_ingested: Mapped[int] = mapped_column(Integer, default=0)
    is_bootstrap: Mapped[bool] = mapped_column(Boolean, default=False)
    errors_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Employee(Base):
    __tablename__ = "employees"

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    manager_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    org_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def _make_raw_model(table_name: str, class_name: str) -> type[Base]:
    """Builds a per-source raw audit model dynamically to avoid four copies."""
    return type(
        class_name,
        (Base,),
        {
            "__tablename__": table_name,
            "source_id": mapped_column(String(255), primary_key=True),
            "fetched_at": mapped_column(
                DateTime(timezone=True), server_default=func.now(), primary_key=True
            ),
            "payload": mapped_column(JSONB),
        },
    )


JiraRaw = _make_raw_model("jira_raw", "JiraRaw")
WizRaw = _make_raw_model("wiz_raw", "WizRaw")
CrowdstrikeRaw = _make_raw_model("crowdstrike_raw", "CrowdstrikeRaw")
VibraniumRaw = _make_raw_model("vibranium_raw", "VibraniumRaw")
```

**Step 2: Generate the initial migration via autogenerate**

```bash
cd /Users/dmar/src/COE-overview
# Make sure postgres is up
docker compose up -d postgres
# Wait for healthy
for i in 1 2 3 4 5 6 7 8 9 10; do
  [ "$(docker inspect -f '{{.State.Health.Status}}' coe-postgres 2>/dev/null)" = "healthy" ] && break
  sleep 2
done
uv run alembic revision --autogenerate --rev-id 0001 -m "initial schema"
```
Expected: a new file appears at `alembic/versions/0001_initial_schema.py`
with `revision: str = "0001"`. Open it and verify it creates
`coe_events` (with the `uq_coe_events_source_sourceid` unique constraint),
`coe_runs`, `employees`, `jira_raw`, `wiz_raw`, `crowdstrike_raw`,
`vibranium_raw`, and the `coe_severity` and `source_enum` enums.

**Step 3: Apply the migration**

```bash
uv run alembic upgrade head
```
Expected: `INFO  [alembic.runtime.migration] Running upgrade  -> 0001`
(or the autogen hash). No errors.

**Step 4: Confirm tables exist**

```bash
docker exec coe-postgres psql -U coe -d coe -c "\dt"
docker exec coe-postgres psql -U coe -d coe -c "\dT"
docker exec coe-postgres psql -U coe -d coe -c "\d coe_events" | grep uq_coe_events
```
Expected `\dt`: lists `alembic_version`, `coe_events`, `coe_runs`,
`crowdstrike_raw`, `employees`, `jira_raw`, `vibranium_raw`, `wiz_raw`.
Expected `\dT`: lists `coe_severity` and `source_enum`.
Expected `\d coe_events`: shows the `uq_coe_events_source_sourceid`
unique constraint.

**Step 5: Commit**

```bash
git add coe/db/models.py alembic/versions/0001_initial_schema.py
git commit -m "feat(db): initial schema with coe_events, coe_runs, employees, raw tables"
```
<!-- END_TASK_6 -->

<!-- START_TASK_7 -->
### Task 7: Add a migration smoke test

**Files:**
- Create: `/Users/dmar/src/COE-overview/tests/test_schema_smoke.py`

**Step 1: Write the test**

```python
"""Smoke test: confirms the schema migrates cleanly into a fresh Postgres.

This is an integration test — it requires docker-compose's postgres to be
running on localhost:5432 with the coe/coe/coe creds.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from coe.config import get_settings


@pytest.mark.integration
async def test_schema_smoke() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            tables = (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY tablename"
                    )
                )
            ).scalars().all()
        expected = {
            "alembic_version",
            "coe_events",
            "coe_runs",
            "crowdstrike_raw",
            "employees",
            "jira_raw",
            "vibranium_raw",
            "wiz_raw",
        }
        assert expected.issubset(set(tables)), (
            f"missing tables: {expected - set(tables)}"
        )
    finally:
        await engine.dispose()
```

**Step 2: Run it**

```bash
cd /Users/dmar/src/COE-overview
uv run pytest tests/test_schema_smoke.py -v
```
Expected: one test passes.

**Step 3: Run the full quality bar**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy coe tests
uv run pytest
```
Expected: all four exit 0. ruff format may need a one-time `uv run ruff
format .` if anything got out of line; if so, re-stage and continue.

**Step 4: Commit**

```bash
git add tests/test_schema_smoke.py
git commit -m "test: smoke test that migrations create all expected tables"
```

**Step 5: Push the branch**

```bash
git push origin coe-overview
```
<!-- END_TASK_7 -->

<!-- END_SUBCOMPONENT_C -->

---

## Done When

- [ ] `uv sync` succeeds.
- [ ] `docker compose up postgres` produces a healthy container within 30s.
- [ ] `alembic upgrade head` applies the initial migration without errors.
- [ ] `\dt` in psql lists all 8 tables (7 + `alembic_version`).
- [ ] `pytest` collects and passes the smoke test.
- [ ] `ruff check`, `ruff format --check`, `mypy coe tests` all exit 0.
- [ ] Branch pushed to `origin/coe-overview`.

## Notes for Subsequent Phases

- Phase 2 will add `coe/ingest/` clients. They will depend on `coe/config.py`
  for credentials (extend `Settings` per source) and `httpx` (already pinned).
- Phase 4 will reuse `coe/db/models.py` for upserts and `coe_runs` writes.
- The `raw` JSON column on `coe_events` lets Phase 2 stash the original
  source record for later debugging without inventing per-source schema.
