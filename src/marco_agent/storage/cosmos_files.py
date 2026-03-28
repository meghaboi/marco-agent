from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceNotFoundError

LOGGER = logging.getLogger(__name__)


class CosmosFileStore:
    def __init__(
        self,
        *,
        endpoint: str | None,
        key: str | None,
        database_name: str,
        container_name: str,
    ) -> None:
        self._container = None
        if not (endpoint and key):
            LOGGER.warning("Cosmos DB not configured; file store running in disabled mode.")
            return

        client = CosmosClient(endpoint, credential=key)
        database = client.create_database_if_not_exists(id=database_name)
        self._container = database.create_container_if_not_exists(
            id=container_name,
            partition_key=PartitionKey(path="/partition_key"),
        )
        LOGGER.info("Cosmos file store ready: %s/%s", database_name, container_name)

    @property
    def enabled(self) -> bool:
        return self._container is not None

    def add_file_record(
        self,
        *,
        user_id: str,
        file_name: str,
        blob_url: str,
        project: str,
        tags: list[str],
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> dict[str, Any]:
        if self._container is None:
            raise RuntimeError("File store unavailable.")
        now = datetime.now(UTC).isoformat()
        file_id = uuid.uuid4().hex[:12]
        item = {
            "id": f"file-{file_id}",
            "partition_key": f"user:{user_id}",
            "kind": "file",
            "file_id": file_id,
            "user_id": user_id,
            "file_name": file_name,
            "blob_url": blob_url,
            "project": project,
            "tags": [tag.strip().lower() for tag in tags if tag.strip()],
            "content_type": content_type,
            "size_bytes": int(size_bytes),
            "sha256": sha256,
            "created_at": now,
            "updated_at": now,
        }
        self._container.upsert_item(item)
        return item

    def get_file(self, *, user_id: str, file_id: str) -> dict[str, Any] | None:
        if self._container is None:
            return None
        query = (
            "SELECT TOP 1 * FROM c "
            "WHERE c.partition_key=@pk AND c.kind='file' AND c.file_id=@file_id"
        )
        rows = list(
            self._container.query_items(
                query=query,
                parameters=[
                    {"name": "@pk", "value": f"user:{user_id}"},
                    {"name": "@file_id", "value": file_id},
                ],
                enable_cross_partition_query=False,
            )
        )
        return rows[0] if rows else None

    def list_files(self, *, user_id: str, project: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if self._container is None:
            return []
        if project:
            query = (
                "SELECT TOP @limit c.file_id, c.file_name, c.project, c.tags, c.created_at, c.blob_url "
                "FROM c WHERE c.partition_key=@pk AND c.kind='file' AND c.project=@project "
                "ORDER BY c.created_at DESC"
            )
            params = [
                {"name": "@limit", "value": int(limit)},
                {"name": "@pk", "value": f"user:{user_id}"},
                {"name": "@project", "value": project},
            ]
        else:
            query = (
                "SELECT TOP @limit c.file_id, c.file_name, c.project, c.tags, c.created_at, c.blob_url "
                "FROM c WHERE c.partition_key=@pk AND c.kind='file' "
                "ORDER BY c.created_at DESC"
            )
            params = [
                {"name": "@limit", "value": int(limit)},
                {"name": "@pk", "value": f"user:{user_id}"},
            ]
        return list(
            self._container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=False,
            )
        )

    def upsert_chunks(
        self,
        *,
        user_id: str,
        file_id: str,
        file_name: str,
        project: str,
        tags: list[str],
        chunks: list[dict[str, Any]],
    ) -> int:
        if self._container is None:
            return 0
        now = datetime.now(UTC).isoformat()
        upserted = 0
        for chunk in chunks:
            chunk_id = str(chunk.get("chunk_id", "")).strip()
            if not chunk_id:
                continue
            item = {
                "id": f"chunk-{file_id}-{chunk_id}",
                "partition_key": f"user:{user_id}",
                "kind": "file_chunk",
                "user_id": user_id,
                "file_id": file_id,
                "file_name": file_name,
                "project": project,
                "tags": [tag.strip().lower() for tag in tags if tag.strip()],
                "chunk_id": chunk_id,
                "content": str(chunk.get("content", "")),
                "start": int(chunk.get("start", 0)),
                "end": int(chunk.get("end", 0)),
                "embedding": chunk.get("embedding", []),
                "updated_at": now,
            }
            self._container.upsert_item(item)
            upserted += 1
        return upserted

    def list_file_chunks(self, *, user_id: str, file_id: str, limit: int = 200) -> list[dict[str, Any]]:
        if self._container is None:
            return []
        query = (
            "SELECT TOP @limit c.file_id, c.file_name, c.chunk_id, c.content, c.start, c.end, c.embedding "
            "FROM c WHERE c.partition_key=@pk AND c.kind='file_chunk' AND c.file_id=@file_id"
        )
        return list(
            self._container.query_items(
                query=query,
                parameters=[
                    {"name": "@limit", "value": int(limit)},
                    {"name": "@pk", "value": f"user:{user_id}"},
                    {"name": "@file_id", "value": file_id},
                ],
                enable_cross_partition_query=False,
            )
        )

    def delete_file(self, *, user_id: str, file_id: str) -> bool:
        if self._container is None:
            return False
        record = self.get_file(user_id=user_id, file_id=file_id)
        if not record:
            return False
        try:
            self._container.delete_item(item=record["id"], partition_key=f"user:{user_id}")
        except CosmosResourceNotFoundError:
            return False
        return True
