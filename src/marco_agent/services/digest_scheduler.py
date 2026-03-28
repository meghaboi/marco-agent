from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from marco_agent.config import AppFileConfig
from marco_agent.services.discord_delivery import DiscordDeliveryService
from marco_agent.services.news_digest import NewsDigestService
from marco_agent.storage.cosmos_digest import CosmosDigestStore

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DigestDispatchResult:
    attempted: int
    generated: int
    skipped: int
    errors: int


class DigestScheduler:
    def __init__(
        self,
        *,
        digest_store: CosmosDigestStore,
        digest_service: NewsDigestService,
        file_config: AppFileConfig,
        discord_delivery: DiscordDeliveryService | None = None,
    ) -> None:
        self._digest_store = digest_store
        self._digest_service = digest_service
        self._file_config = file_config
        self._discord_delivery = discord_delivery

    async def run_due(
        self,
        *,
        reasoning_deployment: str,
        now_utc: datetime | None = None,
        grace_minutes: int = 5,
    ) -> DigestDispatchResult:
        if not self._digest_store.enabled:
            return DigestDispatchResult(attempted=0, generated=0, skipped=0, errors=1)
        now_utc = now_utc or datetime.now(UTC)
        prefs_rows = await asyncio.to_thread(self._digest_store.list_all_preferences)
        attempted = len(prefs_rows)
        generated = 0
        skipped = 0
        errors = 0
        for prefs in prefs_rows:
            try:
                due_key = _due_delivery_key(prefs=prefs, now_utc=now_utc, grace_minutes=grace_minutes)
                if due_key is None:
                    skipped += 1
                    continue
                user_id = str(prefs.get("user_id", "")).strip()
                if not user_id:
                    skipped += 1
                    continue
                if await asyncio.to_thread(self._digest_store.has_delivery_key, user_id=user_id, delivery_key=due_key):
                    skipped += 1
                    continue
                categories = prefs.get("categories")
                if not isinstance(categories, list) or not categories:
                    categories = self._file_config.digest.default_categories
                digest = await self._digest_service.build_and_store_digest(
                    user_id=user_id,
                    deployment=reasoning_deployment,
                    categories=[str(c) for c in categories],
                    max_items=self._file_config.digest.max_items,
                )
                await asyncio.to_thread(
                    self._digest_store.track_delivery,
                    user_id=user_id,
                    digest_id=str(digest.get("digest_id", "")),
                    channel="timer",
                    status="generated",
                    delivery_key=due_key,
                )
                await self._deliver_digest_dm(user_id=user_id, digest=digest)
                generated += 1
            except Exception:
                LOGGER.exception("Digest timer job failed for preferences row: %s", prefs)
                errors += 1
        return DigestDispatchResult(
            attempted=attempted,
            generated=generated,
            skipped=skipped,
            errors=errors,
        )

    async def _deliver_digest_dm(self, *, user_id: str, digest: dict) -> None:
        if self._discord_delivery is None or not self._discord_delivery.enabled:
            return
        await self._discord_delivery.send_dm(user_id=user_id, content=_format_digest_message(digest=digest))

def _due_delivery_key(*, prefs: dict, now_utc: datetime, grace_minutes: int) -> str | None:
    timezone = str(prefs.get("timezone", "UTC")).strip() or "UTC"
    digest_time = str(prefs.get("digest_time_local", "08:30")).strip() or "08:30"
    parts = digest_time.split(":")
    if len(parts) != 2:
        return None
    try:
        target_hour = int(parts[0])
        target_minute = int(parts[1])
        zone = ZoneInfo(timezone)
    except Exception:
        return None

    local_now = now_utc.astimezone(zone)
    target_local = local_now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    delta = local_now - target_local
    if delta < timedelta(0) or delta > timedelta(minutes=max(grace_minutes, 0)):
        return None
    return f"{local_now.date().isoformat()}-{digest_time}"


def _format_digest_message(*, digest: dict[str, object]) -> str:
    summary = str(digest.get("summary", "")).strip() or "Your scheduled digest is ready."
    digest_id = str(digest.get("digest_id", "")).strip() or "unknown"
    categories = digest.get("categories")
    category_text = ", ".join(str(item) for item in categories) if isinstance(categories, list) and categories else "-"
    lines = [
        "Marco Morning Brief",
        f"digest: {digest_id}",
        f"categories: {category_text}",
        "",
        summary,
    ]
    items = digest.get("items")
    if isinstance(items, list) and items:
        lines.append("")
        lines.append("Top stories:")
        for idx, item in enumerate(items[:5], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            source = str(item.get("source", "")).strip()
            if not title:
                continue
            row = f"{idx}. {title}"
            if source:
                row += f" [{source}]"
            if url:
                row += f" {url}"
            lines.append(row)
    return "\n".join(lines).strip()
