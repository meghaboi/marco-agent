from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from typing import Any

LOGGER = logging.getLogger(__name__)


class BlobStorageService:
    def __init__(self, *, connection_string: str | None, container_name: str) -> None:
        self._connection_string = (connection_string or "").strip()
        self._container_name = container_name.strip()

    @property
    def enabled(self) -> bool:
        return bool(self._connection_string and self._container_name)

    async def upload_bytes(
        self,
        *,
        user_id: str,
        file_name: str,
        payload: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Blob storage is not configured.")
        blob_name = _build_blob_name(user_id=user_id, file_name=file_name)
        return await asyncio.to_thread(
            self._upload_bytes_sync,
            blob_name=blob_name,
            payload=payload,
            content_type=content_type,
            metadata=metadata or {},
        )

    def _upload_bytes_sync(
        self,
        *,
        blob_name: str,
        payload: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        try:
            from azure.storage.blob import BlobServiceClient, ContentSettings
        except Exception as exc:
            raise RuntimeError(
                "azure-storage-blob dependency is missing. Install it to enable file upload."
            ) from exc

        client = BlobServiceClient.from_connection_string(self._connection_string)
        container = client.get_container_client(self._container_name)
        try:
            container.create_container()
        except Exception:
            pass
        blob = container.get_blob_client(blob_name)
        content_settings = ContentSettings(content_type=content_type or "application/octet-stream")
        blob.upload_blob(
            payload,
            overwrite=True,
            content_settings=content_settings,
            metadata={k: v for k, v in metadata.items() if k and v},
        )
        props = blob.get_blob_properties()
        return {
            "ok": True,
            "blob_name": blob_name,
            "blob_url": blob.url,
            "etag": str(getattr(props, "etag", "")),
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }


def _build_blob_name(*, user_id: str, file_name: str) -> str:
    safe_name = "".join(ch for ch in file_name if ch.isalnum() or ch in {"-", "_", ".", " "}).strip()
    safe_name = safe_name.replace(" ", "_") or "attachment.bin"
    return f"user-{user_id}/{uuid.uuid4().hex[:10]}-{safe_name}"
