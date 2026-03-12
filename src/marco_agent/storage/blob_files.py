from __future__ import annotations

import logging
from typing import Any

try:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
except ImportError:  # pragma: no cover - optional dependency for non-RAG runtimes
    DefaultAzureCredential = None
    BlobServiceClient = None

LOGGER = logging.getLogger(__name__)


class BlobFileStore:
    def __init__(
        self,
        *,
        account_url: str | None,
        container_name: str,
        connection_string: str | None = None,
    ) -> None:
        self._enabled = bool(connection_string or account_url)
        self._container_client = None
        self._container_name = container_name

        if BlobServiceClient is None:
            self._enabled = False
            LOGGER.warning("azure-storage-blob not installed; file store disabled.")
            return

        if not self._enabled:
            LOGGER.warning("Blob storage not configured; file store disabled.")
            return

        if connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
        else:
            assert account_url
            credential = DefaultAzureCredential() if DefaultAzureCredential is not None else None
            service = BlobServiceClient(account_url=account_url, credential=credential)
        self._container_client = service.get_container_client(container_name)
        self._container_client.create_container(exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def upload_bytes(
        self,
        *,
        blob_path: str,
        payload: bytes,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        if not self._container_client:
            return ""
        settings: dict[str, Any] = {}
        if content_type:
            settings["content_type"] = content_type
        blob = self._container_client.get_blob_client(blob_path)
        blob.upload_blob(payload, overwrite=True, metadata=metadata, **settings)
        return blob.url
