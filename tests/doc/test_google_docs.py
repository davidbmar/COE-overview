"""Unit tests for coe/doc/google_docs.py"""

from unittest import mock

import pytest
from googleapiclient.errors import HttpError

from coe.doc.google_docs import (
    SCOPES,
    DocsClient,
    GoogleDocsError,
    batch_update,
    build_clients_from_file,
    create_doc,
    make_doc_url,
)


class TestGoogleDocsError:
    """Test GoogleDocsError exception."""

    def test_error_stores_status_and_formats_message(self) -> None:
        """GoogleDocsError captures status code and formats message."""
        err = GoogleDocsError(403, "Forbidden access")
        assert err.status == 403
        assert "[google-docs 403]" in str(err)
        assert "Forbidden access" in str(err)

    def test_error_with_500_status(self) -> None:
        """GoogleDocsError handles 500 status."""
        err = GoogleDocsError(500, "Internal server error")
        assert err.status == 500
        assert "[google-docs 500]" in str(err)


class TestScopes:
    """Test SCOPES constant."""

    def test_scopes_include_drive_and_docs(self) -> None:
        """SCOPES includes required drive and documents scopes."""
        assert "https://www.googleapis.com/auth/drive" in SCOPES
        assert "https://www.googleapis.com/auth/documents" in SCOPES


class TestMakeDocUrl:
    """Test make_doc_url helper."""

    def test_generates_correct_url(self) -> None:
        """make_doc_url generates the correct Google Docs URL."""
        url = make_doc_url("abc123")
        assert url == "https://docs.google.com/document/d/abc123/edit"

    def test_handles_different_doc_ids(self) -> None:
        """make_doc_url works with various document IDs."""
        url = make_doc_url("xyz-789")
        assert "xyz-789" in url
        assert "https://docs.google.com/document/d/" in url


class TestCreateDoc:
    """Test create_doc async function."""

    @pytest.mark.asyncio
    async def test_create_doc_success(self) -> None:
        """create_doc returns the document ID on success."""
        mock_drive = mock.MagicMock()
        mock_drive.files.return_value.create.return_value.execute.return_value = {
            "id": "test-doc-123"
        }

        client = DocsClient(drive=mock_drive, docs=None)
        doc_id = await create_doc(client, "Test Doc", "folder-id-123")

        assert doc_id == "test-doc-123"
        mock_drive.files.return_value.create.assert_called_once()
        call_kwargs = mock_drive.files.return_value.create.call_args[1]
        assert call_kwargs["body"]["name"] == "Test Doc"
        assert call_kwargs["body"]["mimeType"] == "application/vnd.google-apps.document"
        assert call_kwargs["body"]["parents"] == ["folder-id-123"]

    @pytest.mark.asyncio
    async def test_create_doc_403_raises_google_docs_error(self) -> None:
        """I2: create_doc raises GoogleDocsError on 403 from drive API with message."""
        mock_drive = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.status = 403
        http_error = HttpError(mock_resp, b"Permission denied")
        mock_drive.files.return_value.create.return_value.execute.side_effect = http_error

        client = DocsClient(drive=mock_drive, docs=None)

        with pytest.raises(GoogleDocsError) as exc_info:
            await create_doc(client, "Test Doc", "folder-id")

        assert exc_info.value.status == 403
        # I2: Verify error message contains expected content
        assert "[google-docs 403]" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_doc_500_raises_google_docs_error(self) -> None:
        """I2: create_doc raises GoogleDocsError on 500 from drive API with message."""
        mock_drive = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.status = 500
        http_error = HttpError(mock_resp, b"Internal server error")
        mock_drive.files.return_value.create.return_value.execute.side_effect = http_error

        client = DocsClient(drive=mock_drive, docs=None)

        with pytest.raises(GoogleDocsError) as exc_info:
            await create_doc(client, "Test Doc", "folder-id")

        assert exc_info.value.status == 500
        # I2: Verify error message contains expected content
        assert "[google-docs 500]" in str(exc_info.value)


class TestBatchUpdate:
    """Test batch_update async function."""

    @pytest.mark.asyncio
    async def test_batch_update_success(self) -> None:
        """batch_update returns None on success."""
        mock_docs = mock.MagicMock()
        mock_docs.documents.return_value.batchUpdate.return_value.execute.return_value = {
            "documentId": "doc-789",
            "replies": [],
        }

        client = DocsClient(drive=None, docs=mock_docs)
        requests: list[dict[str, object]] = [
            {"insertText": {"text": "Hello", "location": {"index": 1}}}
        ]
        await batch_update(client, "doc-789", requests)

        mock_docs.documents.return_value.batchUpdate.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_update_multiple_requests(self) -> None:
        """batch_update handles multiple requests in one call."""
        mock_docs = mock.MagicMock()
        mock_docs.documents.return_value.batchUpdate.return_value.execute.return_value = {
            "documentId": "doc-999",
            "replies": [{}, {}],
        }

        client = DocsClient(drive=None, docs=mock_docs)
        requests: list[dict[str, object]] = [
            {"insertText": {"text": "A", "location": {"index": 1}}},
            {"updateTextStyle": {"range": {"startIndex": 1, "endIndex": 2}}},
        ]
        await batch_update(client, "doc-999", requests)

        # Verify the requests were passed
        call_kwargs = mock_docs.documents.return_value.batchUpdate.call_args[1]
        assert call_kwargs["body"]["requests"] == requests

    @pytest.mark.asyncio
    async def test_batch_update_403_raises_google_docs_error(self) -> None:
        """I2: batch_update raises GoogleDocsError on 403 from docs API with message."""
        mock_docs = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.status = 403
        http_error = HttpError(mock_resp, b"Access forbidden")
        mock_docs.documents.return_value.batchUpdate.return_value.execute.side_effect = http_error

        client = DocsClient(drive=None, docs=mock_docs)
        requests = [{"insertText": {"text": "X", "location": {"index": 1}}}]

        with pytest.raises(GoogleDocsError) as exc_info:
            await batch_update(client, "doc-id", requests)

        assert exc_info.value.status == 403
        # I2: Verify error message contains expected content
        assert "[google-docs 403]" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_batch_update_500_raises_google_docs_error(self) -> None:
        """I2: batch_update raises GoogleDocsError on 500 from docs API with message."""
        mock_docs = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.status = 500
        http_error = HttpError(mock_resp, b"Server error occurred")
        mock_docs.documents.return_value.batchUpdate.return_value.execute.side_effect = http_error

        client = DocsClient(drive=None, docs=mock_docs)
        requests = [{"insertText": {"text": "Y", "location": {"index": 1}}}]

        with pytest.raises(GoogleDocsError) as exc_info:
            await batch_update(client, "doc-id", requests)

        assert exc_info.value.status == 500
        # I2: Verify error message contains expected content
        assert "[google-docs 500]" in str(exc_info.value)


class TestBuildClientsFromFile:
    """Test build_clients_from_file function."""

    def test_build_clients_from_file_structure(self) -> None:
        """build_clients_from_file returns DocsClient with drive and docs services."""
        mock_creds = mock.MagicMock()
        with (
            mock.patch(
                "coe.doc.google_docs.service_account.Credentials.from_service_account_file",
                return_value=mock_creds,
            ),
            mock.patch(
                "coe.doc.google_docs.build",
                side_effect=lambda api, version, **kwargs: mock.MagicMock(),
            ) as mock_build,
        ):
            client = build_clients_from_file("/path/to/service-account.json")

            assert isinstance(client, DocsClient)
            assert client.drive is not None
            assert client.docs is not None
            assert mock_build.call_count == 2

    def test_build_clients_from_file_uses_scopes(self) -> None:
        """build_clients_from_file passes required scopes to credentials."""
        mock_creds = mock.MagicMock()
        with (
            mock.patch(
                "coe.doc.google_docs.service_account.Credentials.from_service_account_file",
                return_value=mock_creds,
            ) as mock_creds_factory,
            mock.patch(
                "coe.doc.google_docs.build",
                side_effect=lambda api, version, **kwargs: mock.MagicMock(),
            ),
        ):
            build_clients_from_file("/path/to/service-account.json")

            call_kwargs = mock_creds_factory.call_args[1]
            assert "scopes" in call_kwargs
            assert list(SCOPES) == call_kwargs["scopes"]
