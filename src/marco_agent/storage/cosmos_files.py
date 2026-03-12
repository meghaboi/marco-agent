from __future__ import annotations

import logging
from datetime import datetime, timezone

from azure.cosmos import CosmosClient, PartitionKey

LOGGER = logging.getLogger(__name__)


class CosmosFileMapStore:
    def __init__(
        self,
        *,
        endpoint: str | None,
        key: str | None,
        database_name: str,
        container_name: str,
    ) -> None:
        self._enabled = bool(endpoint and key)
        self._container = None
        if not self._enabled:
            LOGGER.warning("Cosmos DB not configured; file map store running in disabled mode.")
            return

        client = CosmosClient(endpoint, credential=key)
        database = client.create_database_if_not_exists(id=database_name)
        self._container = database.create_container_if_not_exists(
            id=container_name,
            partition_key=PartitionKey(path="/partition_key"),
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def save_mapping(
        self,
        *,
        user_id: str,
        project_id: str,
        file_id: str,
        filename: str,
        blob_path: str,
        tags: list[str],
    ) -> dict:
        if not self._container:
            return {}
        ts = datetime.now(timezone.utc).isoformat()
        item = {
            "id": file_id,
            "partition_key": f"user:{user_id}",
            "kind": "project_file",
            "user_id": user_id,
            "project_id": project_id,
            "filename": filename,
            "blob_path": blob_path,
            "tags": tags,
            "created_at": ts,
            "updated_at": ts,
        }
        self._container.upsert_item(item)
        return item

    def list_files(self, *, user_id: str, project_id: str | None = None) -> list[dict]:
        if not self._container:
            return []
        query = (
            "SELECT c.id, c.project_id, c.filename, c.blob_path, c.tags, c.created_at "
            "FROM c WHERE c.partition_key = @pk AND c.kind = 'project_file'"
        )
        parameters = [{"name": "@pk", "value": f"user:{user_id}"}]
        if project_id:
            query += " AND c.project_id = @project_id"
            parameters.append({"name": "@project_id", "value": project_id})
        query += " ORDER BY c.created_at DESC"
        return list(
            self._container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=False,
            )
        )
