from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from marco_agent.config import AppFileConfig
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
    ) -> None:
        self._digest_store = digest_store
        self._digest_service = digest_service
        self._file_config = file_config

    async def run_due(self, *, reasoning_deployment: str, now_utc: datetime | None = None) -> DigestDispatchResult:
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
                if not _is_due_now(prefs=prefs, now_utc=now_utc):
                    skipped += 1
                    continue
                user_id = str(prefs.get("user_id", "")).strip()
                if not user_id:
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
                )
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


def _is_due_now(*, prefs: dict, now_utc: datetime) -> bool:
    timezone = str(prefs.get("timezone", "UTC")).strip() or "UTC"
    digest_time = str(prefs.get("digest_time_local", "08:30")).strip() or "08:30"
    parts = digest_time.split(":")
    if len(parts) != 2:
        return False
    try:
        target_hour = int(parts[0])
        target_minute = int(parts[1])
        zone = ZoneInfo(timezone)
    except Exception:
        return False

    local_now = now_utc.astimezone(zone)
    return local_now.hour == target_hour and local_now.minute == target_minute
