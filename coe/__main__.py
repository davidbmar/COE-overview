"""CLI entrypoint for the COE ingest pipeline.

Runs end-to-end: configures logging, loads settings, creates the database
engine and session factory, runs the pipeline, and writes the run_id to
a well-known file for K8s CronJob coordination.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from coe.config import get_settings
from coe.log_capture import configure_structlog
from coe.pipeline import run

# Path used by the K8s CronJob to hand run_id from the ingest container
# to the render container via an emptyDir volume. Overridable via env so
# local dev doesn't need /var/run/coe to exist.
RUN_ID_PATH = Path(os.environ.get("COE_RUN_ID_PATH", "/var/run/coe/last_run_id"))


async def main() -> None:
    """Execute the pipeline and write results.

    Exits with code 0 on success (even if some sources failed), code 1 on
    critical failure that prevents the run from completing at all.
    """
    configure_structlog()
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    result = await run(session_factory, settings)
    print(f"run_id={result.run_id} status={result.status} events={result.events_ingested}")
    await engine.dispose()

    # Hand off the run_id to the downstream render container.
    try:
        RUN_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUN_ID_PATH.write_text(str(result.run_id))
    except OSError as exc:
        # Non-fatal: local dev without /var/run/coe still works. The render
        # container only depends on this file in production K8s; if it's
        # missing it falls back to "latest ok/partial run".
        print(f"warning: could not write {RUN_ID_PATH}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
