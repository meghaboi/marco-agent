from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from hashlib import sha1
from typing import Any, Callable

from marco_agent.ai.foundry import FoundryChatClient
from marco_agent.storage.blob_files import BlobFileStore
from marco_agent.storage.cosmos_files import CosmosFileMapStore
from marco_agent.storage.search_index import AzureSearchChunkStore

_CHUNK_SIZE = 1200
_CHUNK_OVERLAP = 200


@dataclass(slots=True)
class Citation:
    filename: str
    blob_url: str
    chunk_index: int


class FileRagService:
    def __init__(
        self,
        *,
        ai_client: FoundryChatClient,
        blob_store: BlobFileStore,
        search_store: AzureSearchChunkStore,
        file_map_store: CosmosFileMapStore,
        embedding_deployment_resolver: Callable[[], str],
        chat_deployment_resolver: Callable[[], str],
    ) -> None:
        self._ai_client = ai_client
        self._blob_store = blob_store
        self._search_store = search_store
        self._file_map_store = file_map_store
        self._embedding_deployment_resolver = embedding_deployment_resolver
        self._chat_deployment_resolver = chat_deployment_resolver

    @property
    def enabled(self) -> bool:
        return self._blob_store.enabled and self._search_store.enabled

    async def ingest_text_file(
        self,
        *,
        user_id: str,
        project_id: str,
        filename: str,
        content_type: str | None,
        payload: bytes,
        tags: list[str],
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "File RAG storage is not configured."}
        text = _decode_text(payload)
        if not text.strip():
            return {"ok": False, "error": "Attachment has no readable text content."}

        file_id = sha1(f"{user_id}:{project_id}:{filename}:{len(payload)}".encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        blob_path = f"{user_id}/{project_id}/{file_id}/{filename}"
        blob_url = await asyncio.to_thread(
            self._blob_store.upload_bytes,
            blob_path=blob_path,
            payload=payload,
            content_type=content_type,
            metadata={"user_id": user_id, "project_id": project_id, "filename": filename},
        )

        chunks = _chunk_text(text)
        vectors = await self._ai_client.embed_texts(
            deployment=self._embedding_deployment_resolver(),
            texts=chunks,
        )
        documents: list[dict[str, Any]] = []
        for idx, chunk in enumerate(chunks):
            vector = vectors[idx] if idx < len(vectors) else []
            if not vector:
                continue
            documents.append(
                {
                    "id": f"{file_id}-{idx}",
                    "user_id": user_id,
                    "project_id": project_id,
                    "file_id": file_id,
                    "filename": filename,
                    "content": chunk,
                    "blob_url": blob_url,
                    "chunk_index": idx,
                    "tags": tags,
                    "content_vector": vector,
                }
            )
        await asyncio.to_thread(self._search_store.upsert_chunks, chunks=documents)
        await asyncio.to_thread(
            self._file_map_store.save_mapping,
            user_id=user_id,
            project_id=project_id,
            file_id=file_id,
            filename=filename,
            blob_path=blob_path,
            tags=tags,
        )
        return {"ok": True, "file_id": file_id, "chunk_count": len(documents), "blob_url": blob_url}

    async def retrieve_with_citations(
        self,
        *,
        user_id: str,
        query: str,
        project_id: str | None,
        top_k: int = 8,
    ) -> tuple[list[dict[str, Any]], list[Citation]]:
        vectors = await self._ai_client.embed_texts(
            deployment=self._embedding_deployment_resolver(),
            texts=[query],
        )
        if not vectors:
            return [], []
        hits = await asyncio.to_thread(
            self._search_store.vector_search,
            vector=vectors[0],
            user_id=user_id,
            project_id=project_id,
            top_k=top_k,
        )
        citations = [
            Citation(
                filename=str(hit.get("filename", "unknown")),
                blob_url=str(hit.get("blob_url", "")),
                chunk_index=int(hit.get("chunk_index", 0)),
            )
            for hit in hits
        ]
        return hits, citations

    async def summarize(
        self,
        *,
        user_id: str,
        project_id: str,
        focus: str,
    ) -> dict[str, Any]:
        hits, citations = await self.retrieve_with_citations(
            user_id=user_id,
            project_id=project_id,
            query=focus or f"Summarize project {project_id}",
            top_k=10,
        )
        if not hits:
            return {"ok": False, "error": "No indexed project content found."}
        prompt_context = "\n\n".join(
            f"[{idx+1}] {str(hit.get('filename',''))}: {str(hit.get('content',''))}" for idx, hit in enumerate(hits)
        )
        summary = await self._ai_client.chat(
            deployment=self._chat_deployment_resolver(),
            system_prompt="Summarize provided project snippets. Cite source numbers like [1], [2].",
            user_prompt=f"Focus: {focus or 'overall summary'}\n\nSnippets:\n{prompt_context}",
            temperature=0.1,
        )
        return {"ok": True, "summary": summary, "citations": [c.__dict__ for c in citations[:5]]}

    async def compare(
        self,
        *,
        user_id: str,
        project_id: str,
        left_topic: str,
        right_topic: str,
    ) -> dict[str, Any]:
        query = f"Compare {left_topic} versus {right_topic}"
        hits, citations = await self.retrieve_with_citations(user_id=user_id, query=query, project_id=project_id, top_k=12)
        if not hits:
            return {"ok": False, "error": "No indexed project content found."}
        context = "\n\n".join(
            f"[{idx+1}] {str(hit.get('filename',''))}: {str(hit.get('content',''))}" for idx, hit in enumerate(hits)
        )
        comparison = await self._ai_client.chat(
            deployment=self._chat_deployment_resolver(),
            system_prompt="Compare two topics using only provided snippets and cite sources like [1].",
            user_prompt=f"Topic A: {left_topic}\nTopic B: {right_topic}\n\nSnippets:\n{context}",
            temperature=0.1,
        )
        return {"ok": True, "comparison": comparison, "citations": [c.__dict__ for c in citations[:6]]}


def parse_project_and_tags(text: str) -> tuple[str, list[str]]:
    project_match = re.search(r"project:([a-zA-Z0-9_-]+)", text or "")
    project_id = project_match.group(1) if project_match else "default"
    tags_match = re.search(r"tags:([a-zA-Z0-9_,\- ]+)", text or "")
    if not tags_match:
        return project_id, []
    tags = [item.strip() for item in tags_match.group(1).split(",") if item.strip()]
    return project_id, tags


def _chunk_text(text: str) -> list[str]:
    cleaned = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + _CHUNK_SIZE)
        chunks.append(cleaned[start:end])
        if end == len(cleaned):
            break
        start = max(end - _CHUNK_OVERLAP, start + 1)
    return chunks


def _decode_text(payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.decode("utf-8", errors="ignore")
