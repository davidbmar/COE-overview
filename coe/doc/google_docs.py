"""Google Docs API client wrapper."""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

SCOPES = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
)


class GoogleDocsError(Exception):
    """Structured error from Google Docs API calls."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"[google-docs {status}] {message}")
        self.status = status


@dataclass(frozen=True)
class DocsClient:
    """Client pair for Drive and Docs APIs."""

    drive: "Resource"  # Drive v3 resource
    docs: "Resource"  # Docs v1 resource


def build_clients_from_file(service_account_file: str) -> DocsClient:
    """Build Drive and Docs clients from a service account key file.

    Args:
        service_account_file: Path to service account JSON key file.

    Returns:
        DocsClient with authenticated drive and docs services.
    """
    creds = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call,unused-ignore]
        service_account_file, scopes=list(SCOPES)
    )
    return DocsClient(
        drive=build("drive", "v3", credentials=creds, cache_discovery=False),
        docs=build("docs", "v1", credentials=creds, cache_discovery=False),
    )


async def create_doc(client: DocsClient, name: str, folder_id: str) -> str:
    """Create a new Google Doc in a folder.

    Args:
        client: DocsClient with authenticated services.
        name: Document name/title.
        folder_id: Google Drive folder ID to create the doc in.

    Returns:
        The new document's ID.

    Raises:
        GoogleDocsError: If the API call fails.
    """

    def _create() -> str:
        try:
            result = (
                client.drive
                .files()
                .create(
                    body={
                        "name": name,
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": [folder_id],
                    }
                )
                .execute()
            )
            doc_id = result["id"]
            assert isinstance(doc_id, str)
            return doc_id
        except HttpError as e:
            # Extract status code and error message from HttpError
            status = e.resp.status
            # Try to extract reason from the error object
            message = e._get_reason() if hasattr(e, "_get_reason") else str(e)
            raise GoogleDocsError(status, message) from e

    return await asyncio.to_thread(_create)


async def batch_update(
    client: DocsClient, document_id: str, requests: list[dict[str, Any]]
) -> None:
    """Apply batch updates to a Google Doc.

    Args:
        client: DocsClient with authenticated services.
        document_id: The document ID to update.
        requests: List of batchUpdate requests.

    Raises:
        GoogleDocsError: If the API call fails.
    """

    def _batch_update() -> None:
        try:
            client.docs.documents().batchUpdate(
                documentId=document_id, body={"requests": requests}
            ).execute()
        except HttpError as e:
            # Extract status code and error message from HttpError
            status = e.resp.status
            # Try to extract reason from the error object
            message = e._get_reason() if hasattr(e, "_get_reason") else str(e)
            raise GoogleDocsError(status, message) from e

    await asyncio.to_thread(_batch_update)


def make_doc_url(document_id: str) -> str:
    """Generate the shareable URL for a Google Doc.

    Args:
        document_id: The document ID.

    Returns:
        The full Google Docs edit URL.
    """
    return f"https://docs.google.com/document/d/{document_id}/edit"
