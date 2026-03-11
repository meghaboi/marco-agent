from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceNotFoundError

LOGGER = logging.getLogger(__name__)


class CosmosMemoryStore:
    def __init__(
        self,
        *,
        endpoint: str | None,
        key: str | None,
        database_name: str,
        container_name: str,
    ) -> None:
        self._enabled = bool(endpoint and key)
        self._database_name = database_name
        self._container_name = container_name
        self._container = None
        if not self._enabled:
            LOGGER.warning("Cosmos DB not configured; memory store running in disabled mode.")
            return

        client = CosmosClient(endpoint, credential=key)
        database = client.create_database_if_not_exists(id=database_name)
        self._container = database.create_container_if_not_exists(
            id=container_name,
            partition_key=PartitionKey(path="/partition_key"),
        )
        LOGGER.info("Cosmos memory store ready: %s/%s", database_name, container_name)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def save_message(self, *, user_id: str, role: str, content: str) -> None:
        if not self._container:
            return
        item = {
            "id": f"{user_id}-{role}-{datetime.now(UTC).timestamp()}",
            "partition_key": f"user:{user_id}",
            "kind": "conversation",
            "role": role,
            "content": content,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._container.create_item(item)

    def save_unauthorized_attempt(self, *, user_id: str, content: str) -> None:
        if not self._container:
            return
        item = {
            "id": f"unauthorized-{user_id}-{datetime.now(UTC).timestamp()}",
            "partition_key": "security",
            "kind": "unauthorized_attempt",
            "user_id": user_id,
            "content": content,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._container.create_item(item)

    def load_recent_messages(self, *, user_id: str, limit: int) -> list[dict[str, Any]]:
        if not self._container:
            return []
        query = (
            "SELECT TOP @limit c.role, c.content, c.created_at "
            "FROM c WHERE c.partition_key = @pk AND c.kind = 'conversation' "
            "ORDER BY c.created_at DESC"
        )
        parameters = [
            {"name": "@limit", "value": limit},
            {"name": "@pk", "value": f"user:{user_id}"},
        ]
        items = list(
            self._container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=False,
            )
        )
        items.reverse()
        return items

    def delete_all_for_user(self, *, user_id: str) -> int:
        if not self._container:
            return 0

        query = "SELECT c.id FROM c WHERE c.partition_key = @pk"
        items = list(
            self._container.query_items(
                query=query,
                parameters=[{"name": "@pk", "value": f"user:{user_id}"}],
                enable_cross_partition_query=False,
            )
        )
        deleted = 0
        for item in items:
            item_id = item["id"]
            try:
                self._container.delete_item(item=item_id, partition_key=f"user:{user_id}")
                deleted += 1
            except CosmosResourceNotFoundError:
                continue
        return deleted
