"""Entry point for the weekly prep doc renderer.

Resolves run_id in priority order:
1. CLI argument (sys.argv[1])
2. Handoff file ($COE_RUN_ID_PATH, default /var/run/coe/last_run_id)
3. Fallback: most recent ok/partial run (with warning)

Then calls render_weekly_doc to produce the Google Doc.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from coe.config import get_settings
from coe.db.models import CoeRun
from coe.doc.renderer import render_weekly_doc

log = structlog.get_logger()

RUN_ID_PATH = Path(os.environ.get("COE_RUN_ID_PATH", "/var/run/coe/last_run_id"))


async def resolve_run_id(session: AsyncSession) -> int:
    """Resolve run_id in priority order.

    1. CLI argument
    2. Handoff file
    3. Fallback: latest ok/partial run

    Args:
        session: AsyncSession for database queries

    Returns:
        The resolved coe_runs.id

    Raises:
        SystemExit: If no run_id can be determined
    """
    # 1. CLI argument
    if len(sys.argv) > 1:
        return int(sys.argv[1])

    # 2. Handoff file
    if RUN_ID_PATH.is_file():
        return int(RUN_ID_PATH.read_text().strip())

    # 3. Fallback: latest ok/partial run
    log.warning("run-id-fallback", reason="no argv, no emptyDir handoff file")
    row_id = await session.scalar(
        select(CoeRun.id)
        .where(CoeRun.status.in_(["ok", "partial"]))
        .order_by(CoeRun.id.desc())
        .limit(1)
    )
    if row_id is None:
        raise SystemExit("no coe_runs row to render against")
    return row_id


async def main() -> None:
    """Main entry point: resolve run_id and render the doc."""
    settings = get_settings()

    # Create async engine and session
    engine = create_async_engine(
        settings.database_url,
        echo=False,
    )
    async_session_maker: Any = sessionmaker(  # type: ignore[call-overload]
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_maker() as session:
        # Resolve run_id
        run_id = await resolve_run_id(session)

        # Render the doc
        url = await render_weekly_doc(session, settings, run_id)

        log.info("render_complete", run_id=run_id, url=url)


if __name__ == "__main__":
    asyncio.run(main())
