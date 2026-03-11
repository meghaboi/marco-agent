from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError

LOGGER = logging.getLogger(__name__)

VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}


class CosmosTaskStore:
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
            LOGGER.warning("Cosmos DB not configured; task store running in disabled mode.")
            return

        client = CosmosClient(endpoint, credential=key)
        database = client.create_database_if_not_exists(id=database_name)
        self._container = database.create_container_if_not_exists(
            id=container_name,
            partition_key=PartitionKey(path="/partition_key"),
        )
        LOGGER.info("Cosmos task store ready: %s/%s", database_name, container_name)

    @property
    def enabled(self) -> bool:
        return self._container is not None

    def add_task(
        self,
        *,
        user_id: str,
        title: str,
        description: str = "",
        priority: str = "P2",
        due_at: str | None = None,
        tags: list[str] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        if self._container is None:
            raise RuntimeError("Task store is disabled. Configure Cosmos DB first.")

        priority = priority.upper().strip()
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"Invalid priority '{priority}'. Use P0, P1, P2, or P3.")

        task_id = str(uuid.uuid4())[:8]
        now = datetime.now(UTC).isoformat()
        item = {
            "id": task_id,
            "partition_key": f"user:{user_id}",
            "kind": "task",
            "title": title.strip(),
            "description": description.strip(),
            "priority": priority,
            "status": "open",
            "created_at": now,
            "updated_at": now,
            "due_at": due_at,
            "tags": tags or [],
            "notes": notes.strip(),
        }
        self._container.upsert_item(item)
        return item

    def list_tasks(self, *, user_id: str, include_closed: bool = False) -> list[dict[str, Any]]:
        if self._container is None:
            return []
        if include_closed:
            query = (
                "SELECT c.id, c.title, c.priority, c.status, c.due_at, c.created_at "
                "FROM c WHERE c.partition_key = @pk AND c.kind = 'task'"
            )
        else:
            query = (
                "SELECT c.id, c.title, c.priority, c.status, c.due_at, c.created_at "
                "FROM c WHERE c.partition_key = @pk AND c.kind = 'task' AND c.status != 'done'"
            )
        rows = list(
            self._container.query_items(
                query=query,
                parameters=[{"name": "@pk", "value": f"user:{user_id}"}],
                enable_cross_partition_query=False,
            )
        )
        return sorted(rows, key=_task_sort_key)

    def complete_task(self, *, user_id: str, task_id: str) -> bool:
        return self._update_status(user_id=user_id, task_id=task_id, status="done")

    def delete_task(self, *, user_id: str, task_id: str) -> bool:
        if self._container is None:
            return False
        try:
            self._container.delete_item(item=task_id, partition_key=f"user:{user_id}")
            return True
        except CosmosResourceNotFoundError:
            return False

    def _update_status(self, *, user_id: str, task_id: str, status: str) -> bool:
        if self._container is None:
            return False
        try:
            item = self._container.read_item(item=task_id, partition_key=f"user:{user_id}")
        except CosmosHttpResponseError:
            return False
        item["status"] = status
        item["updated_at"] = datetime.now(UTC).isoformat()
        self._container.replace_item(item=task_id, body=item)
        return True


def _task_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    priority = priority_order.get(item.get("priority", "P3"), 3)
    due = item.get("due_at") or "9999-12-31T23:59:59Z"
    created = item.get("created_at") or ""
    return (priority, due, created)
