from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceNotFoundError

LOGGER = logging.getLogger(__name__)


class CosmosDigestStore:
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
            LOGGER.warning("Cosmos DB not configured; digest store running in disabled mode.")
            return

        client = CosmosClient(endpoint, credential=key)
        database = client.create_database_if_not_exists(id=database_name)
        self._container = database.create_container_if_not_exists(
            id=container_name,
            partition_key=PartitionKey(path="/partition_key"),
        )
        LOGGER.info("Cosmos digest store ready: %s/%s", database_name, container_name)

    @property
    def enabled(self) -> bool:
        return self._container is not None

    def upsert_preferences(
        self,
        *,
        user_id: str,
        timezone: str,
        digest_time_local: str,
        categories: list[str],
    ) -> dict[str, Any]:
        if self._container is None:
            raise RuntimeError("Digest store unavailable.")
        now = datetime.now(UTC).isoformat()
        item = {
            "id": "preferences",
            "partition_key": f"user:{user_id}",
            "kind": "digest_preferences",
            "user_id": user_id,
            "timezone": timezone,
            "digest_time_local": digest_time_local,
            "categories": [c.strip().lower() for c in categories if c.strip()],
            "updated_at": now,
        }
        self._container.upsert_item(item)
        return item

    def get_preferences(self, *, user_id: str) -> dict[str, Any] | None:
        if self._container is None:
            return None
        query = (
            "SELECT TOP 1 * FROM c "
            "WHERE c.partition_key=@pk AND c.kind='digest_preferences' AND c.id='preferences'"
        )
        rows = list(
            self._container.query_items(
                query=query,
                parameters=[{"name": "@pk", "value": f"user:{user_id}"}],
                enable_cross_partition_query=False,
            )
        )
        return rows[0] if rows else None

    def list_all_preferences(self) -> list[dict[str, Any]]:
        if self._container is None:
            return []
        query = "SELECT * FROM c WHERE c.kind='digest_preferences'"
        return list(self._container.query_items(query=query, enable_cross_partition_query=True))

    def save_digest(
        self,
        *,
        user_id: str,
        summary: str,
        items: list[dict[str, Any]],
        categories: list[str],
    ) -> dict[str, Any]:
        if self._container is None:
            raise RuntimeError("Digest store unavailable.")
        digest_id = uuid.uuid4().hex[:10]
        now = datetime.now(UTC).isoformat()
        item = {
            "id": f"digest-{digest_id}",
            "partition_key": f"user:{user_id}",
            "kind": "digest",
            "digest_id": digest_id,
            "user_id": user_id,
            "summary": summary,
            "items": items,
            "categories": categories,
            "created_at": now,
        }
        self._container.create_item(item)
        return item

    def get_digest(self, *, user_id: str, digest_id: str) -> dict[str, Any] | None:
        if self._container is None:
            return None
        query = (
            "SELECT TOP 1 * FROM c WHERE c.partition_key=@pk AND c.kind='digest' AND c.digest_id=@digest_id"
        )
        rows = list(
            self._container.query_items(
                query=query,
                parameters=[
                    {"name": "@pk", "value": f"user:{user_id}"},
                    {"name": "@digest_id", "value": digest_id},
                ],
                enable_cross_partition_query=False,
            )
        )
        return rows[0] if rows else None

    def list_recent_digests(self, *, user_id: str, limit: int = 5) -> list[dict[str, Any]]:
        if self._container is None:
            return []
        query = (
            "SELECT TOP @limit c.digest_id, c.summary, c.categories, c.created_at "
            "FROM c WHERE c.partition_key=@pk AND c.kind='digest' ORDER BY c.created_at DESC"
        )
        rows = list(
            self._container.query_items(
                query=query,
                parameters=[
                    {"name": "@pk", "value": f"user:{user_id}"},
                    {"name": "@limit", "value": limit},
                ],
                enable_cross_partition_query=False,
            )
        )
        return rows

    def track_delivery(
        self,
        *,
        user_id: str,
        digest_id: str,
        channel: str,
        status: str,
        delivery_key: str | None = None,
    ) -> None:
        if self._container is None:
            return
        now = datetime.now(UTC).isoformat()
        item = {
            "id": f"delivery-{delivery_key}" if delivery_key else f"delivery-{digest_id}-{uuid.uuid4().hex[:6]}",
            "partition_key": f"user:{user_id}",
            "kind": "digest_delivery",
            "user_id": user_id,
            "digest_id": digest_id,
            "channel": channel,
            "status": status,
            "delivery_key": delivery_key,
            "created_at": now,
        }
        if delivery_key:
            self._container.upsert_item(item)
            return
        self._container.create_item(item)

    def has_delivery_key(self, *, user_id: str, delivery_key: str) -> bool:
        if self._container is None:
            return False
        try:
            self._container.read_item(item=f"delivery-{delivery_key}", partition_key=f"user:{user_id}")
        except CosmosResourceNotFoundError:
            return False
        return True

    def track_open(self, *, user_id: str, digest_id: str, source: str) -> None:
        if self._container is None:
            return
        now = datetime.now(UTC).isoformat()
        item = {
            "id": f"open-{digest_id}-{uuid.uuid4().hex[:6]}",
            "partition_key": f"user:{user_id}",
            "kind": "digest_open",
            "user_id": user_id,
            "digest_id": digest_id,
            "source": source,
            "created_at": now,
        }
        self._container.create_item(item)

    def save_dig_deeper_brief(
        self,
        *,
        user_id: str,
        digest_id: str,
        topic: str,
        brief: str,
        sources: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self._container is None:
            raise RuntimeError("Digest store unavailable.")
        now = datetime.now(UTC).isoformat()
        item = {
            "id": f"digdeeper-{digest_id}-{uuid.uuid4().hex[:8]}",
            "partition_key": f"user:{user_id}",
            "kind": "digest_dig_deeper",
            "user_id": user_id,
            "digest_id": digest_id,
            "topic": topic,
            "brief": brief,
            "sources": sources,
            "created_at": now,
        }
        self._container.create_item(item)
        return item

    def digest_open_rate(self, *, user_id: str, digest_id: str) -> dict[str, int]:
        if self._container is None:
            return {"deliveries": 0, "opens": 0}
        base_params = [
            {"name": "@pk", "value": f"user:{user_id}"},
            {"name": "@digest_id", "value": digest_id},
        ]
        deliveries = list(
            self._container.query_items(
                query=(
                    "SELECT VALUE COUNT(1) FROM c "
                    "WHERE c.partition_key=@pk AND c.kind='digest_delivery' AND c.digest_id=@digest_id"
                ),
                parameters=base_params,
                enable_cross_partition_query=False,
            )
        )
        opens = list(
            self._container.query_items(
                query=(
                    "SELECT VALUE COUNT(1) FROM c "
                    "WHERE c.partition_key=@pk AND c.kind='digest_open' AND c.digest_id=@digest_id"
                ),
                parameters=base_params,
                enable_cross_partition_query=False,
            )
        )
        return {
            "deliveries": int(deliveries[0] if deliveries else 0),
            "opens": int(opens[0] if opens else 0),
        }
