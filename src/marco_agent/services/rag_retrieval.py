from __future__ import annotations

import asyncio
import math
from typing import Any

from marco_agent.ai.foundry import FoundryChatClient
from marco_agent.services.ai_search import AiSearchService
from marco_agent.storage.cosmos_files import CosmosFileStore


class RagRetrievalService:
    def __init__(
        self,
        *,
        ai_client: FoundryChatClient,
        file_store: CosmosFileStore,
        ai_search: AiSearchService,
        embedding_deployment: str,
    ) -> None:
        self._ai_client = ai_client
        self._file_store = file_store
        self._ai_search = ai_search
        self._embedding_deployment = embedding_deployment

    async def retrieve(
        self,
        *,
        user_id: str,
        query: str,
        project: str | None,
        tags: list[str],
        top_k: int = 5,
    ) -> dict[str, Any]:
        query_text = query.strip()
        if not query_text:
            return {"ok": False, "error": "query is required."}

        vectors = await self._ai_client.embed_texts(
            deployment=self._embedding_deployment,
            texts=[query_text],
        )
        query_embedding = vectors[0] if vectors else []
        results: list[dict[str, Any]] = []

        if query_embedding and self._ai_search.enabled:
            results = await self._ai_search.vector_search(
                user_id=user_id,
                query_embedding=query_embedding,
                project=project,
                tags=tags,
                top_k=top_k,
            )
        if not results:
            results = await self._fallback_search(
                user_id=user_id,
                query_embedding=query_embedding,
                project=project,
                tags=tags,
                top_k=top_k,
            )

        citations = [_to_citation(row=row) for row in results[:top_k]]
        return {"ok": True, "query": query_text, "citations": citations, "count": len(citations)}

    async def summarize_file(self, *, user_id: str, file_id: str, deployment: str) -> dict[str, Any]:
        file_row = await asyncio.to_thread(self._file_store.get_file, user_id=user_id, file_id=file_id)
        if not file_row:
            return {"ok": False, "error": f"File '{file_id}' not found."}
        chunks = await asyncio.to_thread(self._file_store.list_file_chunks, user_id=user_id, file_id=file_id, limit=60)
        if not chunks:
            return {"ok": False, "error": f"File '{file_id}' has no indexed text chunks."}
        excerpt = "\n\n".join(str(row.get("content", ""))[:1000] for row in chunks[:8]).strip()
        prompt = (
            "Summarize the following file content and include citation markers like [1], [2].\n"
            "Then list Sources with chunk IDs.\n\n"
            f"file_id: {file_id}\n"
            f"file_name: {file_row.get('file_name', '')}\n\n"
            "Chunks:\n"
            + "\n".join([f"[{idx + 1}] {str(row.get('chunk_id', ''))}: {str(row.get('content', ''))[:400]}" for idx, row in enumerate(chunks[:8])])
            + "\n\nExcerpt:\n"
            + excerpt
        )
        summary = await self._ai_client.chat(
            deployment=deployment,
            system_prompt="You are a grounded analyst. Do not invent sources.",
            user_prompt=prompt,
            temperature=0.1,
        )
        return {
            "ok": True,
            "file_id": file_id,
            "file_name": file_row.get("file_name", ""),
            "summary": summary,
            "sources": [str(row.get("chunk_id", "")) for row in chunks[:8]],
        }

    async def compare_files(
        self,
        *,
        user_id: str,
        file_id_a: str,
        file_id_b: str,
        deployment: str,
    ) -> dict[str, Any]:
        first = await self.summarize_file(user_id=user_id, file_id=file_id_a, deployment=deployment)
        if not first.get("ok"):
            return first
        second = await self.summarize_file(user_id=user_id, file_id=file_id_b, deployment=deployment)
        if not second.get("ok"):
            return second
        prompt = (
            "Compare these two file summaries.\n"
            "Return similarities, differences, and migration risks with short bullets.\n\n"
            f"File A ({file_id_a}):\n{first.get('summary', '')}\n\n"
            f"File B ({file_id_b}):\n{second.get('summary', '')}"
        )
        comparison = await self._ai_client.chat(
            deployment=deployment,
            system_prompt="You produce concise engineering comparisons.",
            user_prompt=prompt,
            temperature=0.15,
        )
        return {
            "ok": True,
            "file_id_a": file_id_a,
            "file_id_b": file_id_b,
            "comparison": comparison,
            "sources": {
                file_id_a: first.get("sources", []),
                file_id_b: second.get("sources", []),
            },
        }

    async def _fallback_search(
        self,
        *,
        user_id: str,
        query_embedding: list[float],
        project: str | None,
        tags: list[str],
        top_k: int,
    ) -> list[dict[str, Any]]:
        files = await asyncio.to_thread(
            self._file_store.list_files,
            user_id=user_id,
            project=project,
            limit=25,
        )
        scored: list[tuple[float, dict[str, Any]]] = []
        for file_row in files:
            file_id = str(file_row.get("file_id", "")).strip()
            if not file_id:
                continue
            chunks = await asyncio.to_thread(
                self._file_store.list_file_chunks,
                user_id=user_id,
                file_id=file_id,
                limit=40,
            )
            for chunk in chunks:
                chunk_tags = chunk.get("tags")
                if tags and isinstance(chunk_tags, list):
                    normalized = {str(tag).strip().lower() for tag in chunk_tags}
                    if not any(tag.lower() in normalized for tag in tags):
                        continue
                score = _cosine_similarity(query_embedding, chunk.get("embedding")) if query_embedding else 0.0
                if score <= 0 and query_embedding:
                    continue
                row = {
                    "file_id": file_id,
                    "file_name": file_row.get("file_name", ""),
                    "chunk_id": chunk.get("chunk_id", ""),
                    "content": chunk.get("content", ""),
                    "blob_url": file_row.get("blob_url", ""),
                    "@search.score": score,
                }
                scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [row for _, row in scored[:top_k]]


def _to_citation(*, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": str(row.get("file_id", "")),
        "file_name": str(row.get("file_name", "")),
        "chunk_id": str(row.get("chunk_id", "")),
        "snippet": str(row.get("content", ""))[:280],
        "source_url": str(row.get("blob_url", "")),
        "score": float(row.get("@search.score", 0.0) or 0.0),
    }


def _cosine_similarity(left: list[float], right_any: Any) -> float:
    if not isinstance(right_any, list):
        return 0.0
    try:
        right = [float(v) for v in right_any]
        left_vec = [float(v) for v in left]
    except (TypeError, ValueError):
        return 0.0
    if not left_vec or not right or len(left_vec) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left_vec, right, strict=True))
    lnorm = math.sqrt(sum(v * v for v in left_vec))
    rnorm = math.sqrt(sum(v * v for v in right))
    if lnorm == 0 or rnorm == 0:
        return 0.0
    return dot / (lnorm * rnorm)
