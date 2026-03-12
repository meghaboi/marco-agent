from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any

from marco_agent.ai.foundry import FoundryChatClient
from marco_agent.config import AppFileConfig
from marco_agent.storage.cosmos_memory import CosmosMemoryStore


@dataclass(slots=True)
class RetrievedMemory:
    role: str
    content: str
    created_at: str
    source: str


class MemoryRetrievalService:
    def __init__(
        self,
        *,
        memory_store: CosmosMemoryStore,
        ai_client: FoundryChatClient,
        file_config: AppFileConfig,
    ) -> None:
        self._memory_store = memory_store
        self._ai_client = ai_client
        self._file_config = file_config

    async def retrieve_context(
        self,
        *,
        user_id: str,
        user_text: str,
        embeddings_deployment: str,
    ) -> list[dict[str, Any]]:
        recent = await asyncio.to_thread(
            self._memory_store.load_recent_messages,
            user_id=user_id,
            limit=self._file_config.assistant.max_memory_messages,
        )
        if not self._file_config.assistant.semantic_memory_enabled:
            return recent

        query_embedding = await self._ai_client.embed_texts(
            deployment=embeddings_deployment,
            texts=[user_text],
        )
        if not query_embedding or not query_embedding[0]:
            return recent

        candidates = await asyncio.to_thread(
            self._memory_store.load_embedding_candidates,
            user_id=user_id,
            limit=max(self._file_config.assistant.max_semantic_memory_messages * 6, 20),
        )
        semantic_messages = _pick_semantic_matches(
            query_vector=query_embedding[0],
            candidates=candidates,
            limit=self._file_config.assistant.max_semantic_memory_messages,
            threshold=self._file_config.retrieval.semantic_similarity_threshold,
        )

        merged: list[dict[str, Any]] = []
        seen = set()
        for item in recent + semantic_messages:
            role = str(item.get("role", "")).strip().lower()
            content = str(item.get("content", "")).strip()
            created_at = str(item.get("created_at", "")).strip()
            key = (role, content, created_at)
            if role in {"user", "assistant"} and content and key not in seen:
                seen.add(key)
                merged.append(
                    {
                        "role": role,
                        "content": content,
                        "created_at": created_at,
                    }
                )
        return merged


def _pick_semantic_matches(
    *,
    query_vector: list[float],
    candidates: list[dict[str, Any]],
    limit: int,
    threshold: float,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in candidates:
        vector = item.get("embedding")
        if not isinstance(vector, list):
            continue
        try:
            score = _cosine_similarity(query_vector, [float(v) for v in vector])
        except (TypeError, ValueError):
            continue
        if score >= threshold:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in scored[:limit]]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(left_v * right_v for left_v, right_v in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(v * v for v in left))
    right_norm = math.sqrt(sum(v * v for v in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
