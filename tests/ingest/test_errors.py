"""Tests for shared ingest error types."""

from __future__ import annotations

import pytest

from coe.ingest.errors import AuthError, IngestError, TransientError


@pytest.mark.unit
class TestIngestError:
    """Tests for IngestError base class."""

    def test_ingest_error_stores_source_and_message(self) -> None:
        """IngestError stores source and message attributes."""
        error = IngestError("jira", "Connection failed")
        assert error.source == "jira"
        assert error.message == "Connection failed"

    def test_ingest_error_formats_message_with_source_tag(self) -> None:
        """IngestError exception message includes source tag in brackets."""
        error = IngestError("wiz", "Invalid credentials")
        assert str(error) == "[wiz] Invalid credentials"

    def test_ingest_error_is_exception(self) -> None:
        """IngestError is an Exception subclass."""
        error = IngestError("test", "msg")
        assert isinstance(error, Exception)


@pytest.mark.unit
class TestAuthError:
    """Tests for AuthError subclass."""

    def test_auth_error_inherits_from_ingest_error(self) -> None:
        """AuthError is a subclass of IngestError."""
        error = AuthError("jira", "HTTP 401")
        assert isinstance(error, IngestError)

    def test_auth_error_stores_source_and_message(self) -> None:
        """AuthError stores source and message attributes."""
        error = AuthError("crowdstrike", "Unauthorized")
        assert error.source == "crowdstrike"
        assert error.message == "Unauthorized"

    def test_auth_error_formats_message_with_source_tag(self) -> None:
        """AuthError exception message includes source tag."""
        error = AuthError("hr", "HTTP 403")
        assert str(error) == "[hr] HTTP 403"

    def test_auth_error_distinguishable_from_transient_error(self) -> None:
        """AuthError can be distinguished from TransientError via isinstance."""
        auth_err = AuthError("jira", "401")
        transient_err = TransientError("jira", "500", last_status=500, retries_attempted=3)
        assert isinstance(auth_err, AuthError)
        assert not isinstance(auth_err, TransientError)
        assert isinstance(transient_err, TransientError)
        assert not isinstance(transient_err, AuthError)


@pytest.mark.unit
class TestTransientError:
    """Tests for TransientError subclass."""

    def test_transient_error_inherits_from_ingest_error(self) -> None:
        """TransientError is a subclass of IngestError."""
        error = TransientError("wiz", "Server error", last_status=500, retries_attempted=3)
        assert isinstance(error, IngestError)

    def test_transient_error_stores_all_attributes(self) -> None:
        """TransientError stores source, message, last_status, and retries_attempted."""
        error = TransientError(
            "vibranium", "Connection timeout", last_status=503, retries_attempted=5
        )
        assert error.source == "vibranium"
        assert error.message == "Connection timeout"
        assert error.last_status == 503
        assert error.retries_attempted == 5

    def test_transient_error_formats_message_with_source_tag(self) -> None:
        """TransientError exception message includes source tag."""
        error = TransientError("hr", "HTTP 500", last_status=500, retries_attempted=2)
        assert str(error) == "[hr] HTTP 500"

    def test_transient_error_with_none_last_status(self) -> None:
        """TransientError can have last_status=None for transport errors."""
        error = TransientError("jira", "Connection error", last_status=None, retries_attempted=4)
        assert error.last_status is None
        assert error.retries_attempted == 4

    def test_transient_error_distinguishable_from_auth_error(self) -> None:
        """TransientError can be distinguished from AuthError via isinstance."""
        transient_err = TransientError("jira", "500", last_status=500, retries_attempted=3)
        auth_err = AuthError("jira", "401")
        assert isinstance(transient_err, TransientError)
        assert not isinstance(transient_err, AuthError)
        assert isinstance(auth_err, AuthError)
        assert not isinstance(auth_err, TransientError)
