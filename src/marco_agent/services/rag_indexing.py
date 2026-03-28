from __future__ import annotations

import asyncio
from typing import Any

from marco_agent.ai.foundry import FoundryChatClient
from marco_agent.services.ai_search import AiSearchService
from marco_agent.storage.cosmos_files import CosmosFileStore


class RagIndexingService:
    def __init__(
        self,
        *,
        ai_client: FoundryChatClient,
        file_store: CosmosFileStore,
        ai_search: AiSearchService,
        embedding_deployment: str,
        chunk_size_chars: int,
        chunk_overlap_chars: int,
        max_chunks_per_file: int,
    ) -> None:
        self._ai_client = ai_client
        self._file_store = file_store
        self._ai_search = ai_search
        self._embedding_deployment = embedding_deployment
        self._chunk_size = max(300, int(chunk_size_chars))
        self._chunk_overlap = max(0, min(int(chunk_overlap_chars), self._chunk_size // 2))
        self._max_chunks = max(1, int(max_chunks_per_file))

    async def index_text_file(
        self,
        *,
        user_id: str,
        file_id: str,
        file_name: str,
        blob_url: str,
        text: str,
        project: str,
        tags: list[str],
    ) -> dict[str, Any]:
        chunks = _chunk_text(
            text=text,
            chunk_size=self._chunk_size,
            overlap=self._chunk_overlap,
            limit=self._max_chunks,
        )
        if not chunks:
            return {"ok": True, "chunk_count": 0, "indexed_count": 0}

        embeddings = await self._ai_client.embed_texts(
            deployment=self._embedding_deployment,
            texts=[chunk["content"] for chunk in chunks],
        )
        if len(embeddings) != len(chunks):
            embeddings = [[] for _ in chunks]
        for idx, chunk in enumerate(chunks):
            chunk["embedding"] = embeddings[idx]

        await asyncio.to_thread(
            self._file_store.upsert_chunks,
            user_id=user_id,
            file_id=file_id,
            file_name=file_name,
            project=project,
            tags=tags,
            chunks=chunks,
        )

        docs = [
            {
                "id": f"{file_id}-{chunk['chunk_id']}",
                "user_id": user_id,
                "file_id": file_id,
                "file_name": file_name,
                "chunk_id": chunk["chunk_id"],
                "content": chunk["content"],
                "project": project,
                "tags": tags,
                "blob_url": blob_url,
                "embedding": chunk["embedding"],
            }
            for chunk in chunks
            if isinstance(chunk.get("embedding"), list) and chunk["embedding"]
        ]
        indexed = 0
        if docs and self._ai_search.enabled:
            ok = await self._ai_search.upsert_documents(documents=docs)
            indexed = len(docs) if ok else 0
        return {"ok": True, "chunk_count": len(chunks), "indexed_count": indexed}


def _chunk_text(*, text: str, chunk_size: int, overlap: int, limit: int) -> list[dict[str, Any]]:
    normalized = text.strip()
    if not normalized:
        return []
    chunks: list[dict[str, Any]] = []
    start = 0
    step = max(1, chunk_size - overlap)
    while start < len(normalized) and len(chunks) < limit:
        end = min(len(normalized), start + chunk_size)
        content = normalized[start:end].strip()
        if content:
            chunks.append(
                {
                    "chunk_id": f"c{len(chunks) + 1}",
                    "content": content,
                    "start": start,
                    "end": end,
                }
            )
        if end >= len(normalized):
            break
        start += step
    return chunks
