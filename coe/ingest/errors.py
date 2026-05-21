"""Shared ingest error types for structured exception handling across sources."""

from __future__ import annotations


class IngestError(Exception):
    """Base exception for ingest operations. Includes source identifier."""

    def __init__(self, source: str, message: str) -> None:
        """Initialize IngestError with source and message.

        Args:
            source: The data source that failed (e.g., 'jira', 'wiz').
            message: Human-readable error description.
        """
        super().__init__(f"[{source}] {message}")
        self.source = source
        self.message = message


class AuthError(IngestError):
    """Authentication failed (401/403). Permanent; do not retry."""

    pass


class TransientError(IngestError):
    """Retries exhausted on a retriable status (408/429/5xx) or transport error."""

    def __init__(
        self,
        source: str,
        message: str,
        last_status: int | None,
        retries_attempted: int,
    ) -> None:
        """Initialize TransientError with retry metadata.

        Args:
            source: The data source that failed.
            message: Human-readable error description.
            last_status: The final HTTP status code, or None if transport error.
            retries_attempted: Total number of retry attempts made.
        """
        super().__init__(source, message)
        self.last_status = last_status
        self.retries_attempted = retries_attempted
