from __future__ import annotations

import asyncio
import logging
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
import xml.etree.ElementTree as ET

import aiohttp

from marco_agent.ai.foundry import FoundryChatClient
from marco_agent.storage.cosmos_digest import CosmosDigestStore

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class NewsItem:
    title: str
    url: str
    source: str
    published_at: str
    category: str


class NewsDigestService:
    def __init__(
        self,
        *,
        ai_client: FoundryChatClient,
        digest_store: CosmosDigestStore,
        rss_url_template: str,
    ) -> None:
        self._ai_client = ai_client
        self._digest_store = digest_store
        self._rss_url_template = rss_url_template

    async def build_and_store_digest(
        self,
        *,
        user_id: str,
        deployment: str,
        categories: list[str],
        max_items: int,
    ) -> dict[str, Any]:
        categories = [c.strip().lower() for c in categories if c.strip()]
        if not categories:
            categories = ["world", "technology"]
        items = await self.fetch_news(categories=categories, max_items=max_items)
        summary = await self._compose_grounded_summary(deployment=deployment, items=items)
        serialized_items = [asdict(item) for item in items]
        record = await asyncio.to_thread(
            self._digest_store.save_digest,
            user_id=user_id,
            summary=summary,
            items=serialized_items,
            categories=categories,
        )
        return {
            "digest_id": record.get("digest_id", ""),
            "summary": summary,
            "items": serialized_items,
            "categories": categories,
            "created_at": record.get("created_at", datetime.now(UTC).isoformat()),
        }

    async def fetch_news(self, *, categories: list[str], max_items: int) -> list[NewsItem]:
        per_category = max(3, min(8, max_items))
        tasks = [self._fetch_category(category=category, limit=per_category) for category in categories]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: list[NewsItem] = []
        seen = set()
        for result in results:
            if isinstance(result, Exception):
                LOGGER.warning("News RSS fetch failed: %s", result)
                continue
            for item in result:
                if item.url in seen:
                    continue
                seen.add(item.url)
                merged.append(item)
        merged.sort(key=lambda item: item.published_at or "", reverse=True)
        return merged[:max_items]

    async def _fetch_category(self, *, category: str, limit: int) -> list[NewsItem]:
        query = urllib.parse.quote_plus(f"{category} latest news")
        url = self._rss_url_template.format(query=query)
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                body = await response.text()
        root = ET.fromstring(body)
        rows: list[NewsItem] = []
        items = root.findall("./channel/item")
        for entry in items[:limit]:
            title = (entry.findtext("title") or "").strip()
            link = (entry.findtext("link") or "").strip()
            published = (entry.findtext("pubDate") or "").strip()
            source = (entry.findtext("source") or "").strip()
            if not title or not link:
                continue
            rows.append(
                NewsItem(
                    title=title,
                    url=link,
                    source=source or "Unknown",
                    published_at=published,
                    category=category,
                )
            )
        return rows

    async def dig_deeper(
        self,
        *,
        user_id: str,
        deployment: str,
        digest_id: str,
        topic: str,
    ) -> dict[str, Any]:
        digest = await asyncio.to_thread(self._digest_store.get_digest, user_id=user_id, digest_id=digest_id)
        if not digest:
            return {"ok": False, "error": f"Digest '{digest_id}' not found."}
        items = digest.get("items")
        if not isinstance(items, list):
            items = []
        ranked = _topic_filter(topic=topic, items=items)
        brief = await self._compose_deeper_brief(
            deployment=deployment,
            topic=topic,
            items=ranked[:5],
        )
        record = await asyncio.to_thread(
            self._digest_store.save_dig_deeper_brief,
            user_id=user_id,
            digest_id=digest_id,
            topic=topic,
            brief=brief,
            sources=ranked[:5],
        )
        return {
            "ok": True,
            "digest_id": digest_id,
            "topic": topic,
            "brief": brief,
            "sources": ranked[:5],
            "created_at": record.get("created_at"),
        }

    async def _compose_grounded_summary(self, *, deployment: str, items: list[NewsItem]) -> str:
        if not items:
            return "No major headlines matched your categories today."
        source_rows = []
        for idx, item in enumerate(items, start=1):
            source_rows.append(
                f"[{idx}] {item.title} | source={item.source} | category={item.category} | url={item.url}"
            )
        prompt = (
            "Create a concise morning digest in bullet form.\n"
            "Rules:\n"
            "- Use only the provided headlines.\n"
            "- Include inline citations like [1], [2].\n"
            "- End with a 'Sources' section mapping citation to URL.\n\n"
            "Headlines:\n"
            + "\n".join(source_rows)
        )
        return await self._ai_client.chat(
            deployment=deployment,
            system_prompt="You are a precise analyst. Never invent sources.",
            user_prompt=prompt,
            temperature=0.1,
        )

    async def _compose_deeper_brief(
        self,
        *,
        deployment: str,
        topic: str,
        items: list[dict[str, Any]],
    ) -> str:
        if not items:
            return f"No source evidence found for '{topic}' in that digest."
        source_rows = []
        for idx, item in enumerate(items, start=1):
            title = str(item.get("title", "")).strip()
            source = str(item.get("source", "")).strip()
            category = str(item.get("category", "")).strip()
            url = str(item.get("url", "")).strip()
            source_rows.append(f"[{idx}] {title} | source={source} | category={category} | url={url}")
        prompt = (
            f"Re-brief this topic with more depth: {topic}\n"
            "Rules:\n"
            "- Use only provided items.\n"
            "- Explain why this matters now.\n"
            "- Include citations [1], [2] and a Sources list.\n\n"
            "Items:\n"
            + "\n".join(source_rows)
        )
        return await self._ai_client.chat(
            deployment=deployment,
            system_prompt="You produce grounded briefs with explicit source attribution.",
            user_prompt=prompt,
            temperature=0.15,
        )


def _topic_filter(topic: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    topic_terms = {part.strip().lower() for part in topic.split() if part.strip()}
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in items:
        text = " ".join(
            [
                str(item.get("title", "")).lower(),
                str(item.get("category", "")).lower(),
                str(item.get("source", "")).lower(),
            ]
        )
        score = sum(1 for term in topic_terms if term in text)
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]
