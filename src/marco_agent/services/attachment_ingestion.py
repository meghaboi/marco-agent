from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import aiohttp

from marco_agent.services.blob_storage import BlobStorageService
from marco_agent.services.rag_indexing import RagIndexingService
from marco_agent.storage.cosmos_files import CosmosFileStore


class AttachmentIngestionService:
    def __init__(
        self,
        *,
        blob_storage: BlobStorageService,
        file_store: CosmosFileStore,
        rag_indexing: RagIndexingService,
        default_project: str,
        max_file_size_mb: int,
    ) -> None:
        self._blob_storage = blob_storage
        self._file_store = file_store
        self._rag_indexing = rag_indexing
        self._default_project = default_project
        self._max_size_bytes = max(1, max_file_size_mb) * 1024 * 1024

    @property
    def enabled(self) -> bool:
        return self._blob_storage.enabled and self._file_store.enabled

    async def ingest_discord_attachments(
        self,
        *,
        user_id: str,
        attachments: list[Any],
        project: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return [{"ok": False, "error": "RAG ingestion unavailable: blob/file stores not configured."}]
        work = [
            self._ingest_one(
                user_id=user_id,
                attachment=attachment,
                project=(project or self._default_project).strip(),
                tags=tags or [],
            )
            for attachment in attachments
        ]
        return await asyncio.gather(*work)

    async def ingest_text(
        self,
        *,
        user_id: str,
        file_name: str,
        text: str,
        project: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = text.encode("utf-8", errors="ignore")
        upload = await self._blob_storage.upload_bytes(
            user_id=user_id,
            file_name=file_name,
            payload=payload,
            content_type="text/plain; charset=utf-8",
            metadata={"source": "tool:rag_ingest_text"},
        )
        record = await asyncio.to_thread(
            self._file_store.add_file_record,
            user_id=user_id,
            file_name=file_name,
            blob_url=str(upload.get("blob_url", "")),
            project=(project or self._default_project).strip(),
            tags=tags or [],
            content_type="text/plain",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        file_id = str(record.get("file_id", ""))
        index_result = await self._rag_indexing.index_text_file(
            user_id=user_id,
            file_id=file_id,
            file_name=file_name,
            blob_url=str(upload.get("blob_url", "")),
            text=text,
            project=str(record.get("project", self._default_project)),
            tags=[str(tag) for tag in record.get("tags", []) if str(tag).strip()],
        )
        return {
            "ok": True,
            "file_id": file_id,
            "file_name": file_name,
            "blob_url": str(upload.get("blob_url", "")),
            "chunk_count": int(index_result.get("chunk_count", 0)),
            "indexed_count": int(index_result.get("indexed_count", 0)),
        }

    async def _ingest_one(
        self,
        *,
        user_id: str,
        attachment: Any,
        project: str,
        tags: list[str],
    ) -> dict[str, Any]:
        file_name = str(getattr(attachment, "filename", "")).strip() or "attachment.bin"
        content_type = str(getattr(attachment, "content_type", "")).strip() or "application/octet-stream"
        size = int(getattr(attachment, "size", 0) or 0)
        if size > self._max_size_bytes:
            return {"ok": False, "file_name": file_name, "error": "Attachment exceeds max file size."}
        source_url = str(getattr(attachment, "url", "")).strip()
        if not source_url:
            return {"ok": False, "file_name": file_name, "error": "Attachment URL missing."}

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(source_url) as response:
                if response.status >= 300:
                    return {"ok": False, "file_name": file_name, "error": f"Attachment download failed ({response.status})."}
                payload = await response.read()
        upload = await self._blob_storage.upload_bytes(
            user_id=user_id,
            file_name=file_name,
            payload=payload,
            content_type=content_type,
            metadata={"source": "discord_attachment"},
        )
        record = await asyncio.to_thread(
            self._file_store.add_file_record,
            user_id=user_id,
            file_name=file_name,
            blob_url=str(upload.get("blob_url", "")),
            project=project,
            tags=tags,
            content_type=content_type,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        file_id = str(record.get("file_id", ""))
        text = _decode_text_payload(payload=payload, content_type=content_type)
        chunk_count = 0
        indexed_count = 0
        if text:
            indexed = await self._rag_indexing.index_text_file(
                user_id=user_id,
                file_id=file_id,
                file_name=file_name,
                blob_url=str(upload.get("blob_url", "")),
                text=text,
                project=project,
                tags=tags,
            )
            chunk_count = int(indexed.get("chunk_count", 0))
            indexed_count = int(indexed.get("indexed_count", 0))
        return {
            "ok": True,
            "file_id": file_id,
            "file_name": file_name,
            "blob_url": str(upload.get("blob_url", "")),
            "chunk_count": chunk_count,
            "indexed_count": indexed_count,
        }


def _decode_text_payload(*, payload: bytes, content_type: str) -> str:
    lowered = content_type.lower()
    if "application/pdf" in lowered:
        return _extract_pdf_text(payload)
    if (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in lowered
        or "application/msword" in lowered
    ):
        return _extract_docx_text(payload)
    if "text" in lowered or "json" in lowered or "xml" in lowered or "yaml" in lowered:
        return payload.decode("utf-8", errors="ignore")
    return ""


def _extract_pdf_text(payload: bytes) -> str:
    try:
        from io import BytesIO

        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(BytesIO(payload))
    except Exception:
        return ""
    rows = []
    for page in reader.pages[:50]:
        try:
            rows.append((page.extract_text() or "").strip())
        except Exception:
            continue
    return "\n".join(row for row in rows if row).strip()


def _extract_docx_text(payload: bytes) -> str:
    try:
        from io import BytesIO

        from docx import Document
    except Exception:
        return ""
    try:
        document = Document(BytesIO(payload))
    except Exception:
        return ""
    rows = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    return "\n".join(rows).strip()
