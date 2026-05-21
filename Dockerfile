# syntax=docker/dockerfile:1.7

# ------ Build stage ------
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install deps in a separate layer for caching
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-editable

# Install the project itself
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable

# ------ Runtime stage ------
FROM python:3.12-slim

RUN useradd -m -u 1000 coe \
 && mkdir /app \
 && chown coe:coe /app

COPY --from=builder --chown=coe:coe /app/.venv /app/.venv
COPY --from=builder --chown=coe:coe /app/coe /app/coe
COPY --from=builder --chown=coe:coe /app/alembic /app/alembic
COPY --from=builder --chown=coe:coe /app/alembic.ini /app/alembic.ini

USER coe
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

# Default entrypoint is the ingest pipeline; the doc job overrides
# `command` in the K8s manifest.
ENTRYPOINT ["python", "-m", "coe"]
