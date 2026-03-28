from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from marco_agent.services.digest_scheduler import _due_delivery_key, _format_digest_message, DigestScheduler


class StubDigestStore:
    def __init__(self) -> None:
        self.enabled = True
        self.preferences = []
        self.delivery_keys: set[tuple[str, str]] = set()
        self.deliveries: list[dict] = []

    def list_all_preferences(self) -> list[dict]:
        return list(self.preferences)

    def has_delivery_key(self, *, user_id: str, delivery_key: str) -> bool:
        return (user_id, delivery_key) in self.delivery_keys

    def track_delivery(self, *, user_id: str, digest_id: str, channel: str, status: str, delivery_key: str | None = None) -> None:
        self.deliveries.append(
            {
                "user_id": user_id,
                "digest_id": digest_id,
                "channel": channel,
                "status": status,
                "delivery_key": delivery_key,
            }
        )
        if delivery_key:
            self.delivery_keys.add((user_id, delivery_key))


class StubDigestService:
    def __init__(self) -> None:
        self.calls = 0

    async def build_and_store_digest(self, *, user_id: str, deployment: str, categories: list[str], max_items: int) -> dict:
        self.calls += 1
        _ = (deployment, max_items, user_id)
        return {
            "digest_id": f"dg-{self.calls}",
            "summary": "Summary [1]",
            "items": [{"title": "Story", "source": "Example", "url": "https://example.com"}],
            "categories": categories,
        }


class StubDiscordDelivery:
    def __init__(self) -> None:
        self.enabled = True
        self.messages: list[tuple[str, str]] = []

    async def send_dm(self, *, user_id: str, content: str) -> None:
        self.messages.append((user_id, content))


class StubFileConfig:
    class Digest:
        default_categories = ["world"]
        max_items = 5

    digest = Digest()


def test_due_delivery_key_matches_within_grace_window() -> None:
    now_utc = datetime(2026, 3, 21, 1, 46, tzinfo=UTC)  # 07:16 IST
    prefs = {"timezone": "Asia/Kolkata", "digest_time_local": "07:15"}
    assert _due_delivery_key(prefs=prefs, now_utc=now_utc, grace_minutes=5) == "2026-03-21-07:15"


def test_due_delivery_key_rejects_after_grace_window() -> None:
    now_utc = datetime(2026, 3, 21, 1, 52, tzinfo=UTC)  # 07:22 IST
    prefs = {"timezone": "Asia/Kolkata", "digest_time_local": "07:15"}
    assert _due_delivery_key(prefs=prefs, now_utc=now_utc, grace_minutes=5) is None


def test_scheduler_generates_once_per_delivery_key() -> None:
    store = StubDigestStore()
    store.preferences.append(
        {
            "user_id": "u1",
            "timezone": "Asia/Kolkata",
            "digest_time_local": "07:15",
            "categories": ["ai"],
        }
    )
    service = StubDigestService()
    delivery = StubDiscordDelivery()
    scheduler = DigestScheduler(
        digest_store=store,  # type: ignore[arg-type]
        digest_service=service,  # type: ignore[arg-type]
        file_config=StubFileConfig(),  # type: ignore[arg-type]
        discord_delivery=delivery,  # type: ignore[arg-type]
    )
    now_utc = datetime(2026, 3, 21, 1, 46, tzinfo=UTC)

    first = asyncio.run(scheduler.run_due(reasoning_deployment="x", now_utc=now_utc))
    second = asyncio.run(scheduler.run_due(reasoning_deployment="x", now_utc=now_utc))

    assert first.generated == 1
    assert second.generated == 0
    assert service.calls == 1
    assert len(delivery.messages) == 1


def test_format_digest_message_includes_top_story() -> None:
    text = _format_digest_message(
        digest={
            "digest_id": "dg1",
            "summary": "Summary [1]",
            "categories": ["ai"],
            "items": [{"title": "Story", "source": "Example", "url": "https://example.com"}],
        }
    )
    assert "Marco Morning Brief" in text
    assert "https://example.com" in text
