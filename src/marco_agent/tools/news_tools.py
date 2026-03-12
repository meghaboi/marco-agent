from __future__ import annotations

import asyncio
import json
from typing import Any
from zoneinfo import ZoneInfo

from marco_agent.services.news_digest import NewsDigestService
from marco_agent.storage.cosmos_digest import CosmosDigestStore

NEWS_TOOL_NAMES = {
    "digest_preferences_set",
    "digest_preferences_get",
    "digest_generate_now",
    "digest_recent_list",
    "digest_open",
    "digest_dig_deeper",
}


def news_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "digest_preferences_set",
                "description": "Set digest time, timezone, and categories for daily news digest.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "digest_time_local": {
                            "type": "string",
                            "description": "HH:MM (24-hour) local time.",
                        },
                        "timezone": {
                            "type": "string",
                            "description": "IANA timezone, e.g. Asia/Calcutta or America/New_York.",
                        },
                        "categories": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["digest_time_local", "timezone", "categories"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "digest_preferences_get",
                "description": "Read current digest preferences.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "digest_generate_now",
                "description": "Generate a grounded digest immediately using configured categories.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "categories": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional override categories.",
                        },
                        "max_items": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "digest_recent_list",
                "description": "List recent digests and IDs.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "digest_open",
                "description": "Mark a digest as opened and return current open rate.",
                "parameters": {
                    "type": "object",
                    "properties": {"digest_id": {"type": "string"}},
                    "required": ["digest_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "digest_dig_deeper",
                "description": "Generate a deeper re-brief for a digest topic with citations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "digest_id": {"type": "string"},
                        "topic": {"type": "string"},
                    },
                    "required": ["digest_id", "topic"],
                    "additionalProperties": False,
                },
            },
        },
    ]


async def execute_news_tool_call(
    *,
    digest_store: CosmosDigestStore,
    digest_service: NewsDigestService,
    user_id: str,
    tool_name: str,
    arguments_json: str,
    default_categories: list[str],
    default_max_items: int,
    reasoning_deployment: str,
) -> dict[str, Any]:
    args = _load_tool_args(arguments_json)
    if tool_name not in NEWS_TOOL_NAMES:
        return {"ok": False, "error": f"Unknown news tool '{tool_name}'."}
    if not digest_store.enabled:
        return {"ok": False, "error": "Digest store is unavailable. Configure Cosmos DB."}

    try:
        if tool_name == "digest_preferences_set":
            digest_time_local = str(args.get("digest_time_local", "")).strip()
            timezone = str(args.get("timezone", "")).strip()
            categories = _as_str_list(args.get("categories"))
            if not digest_time_local or not timezone or not categories:
                return {"ok": False, "error": "digest_time_local, timezone, and categories are required."}
            if not _is_valid_time_hhmm(digest_time_local):
                return {"ok": False, "error": "digest_time_local must be HH:MM in 24-hour format."}
            if not _is_valid_timezone(timezone):
                return {"ok": False, "error": "timezone must be a valid IANA timezone."}
            prefs = await asyncio.to_thread(
                digest_store.upsert_preferences,
                user_id=user_id,
                timezone=timezone,
                digest_time_local=digest_time_local,
                categories=categories,
            )
            return {"ok": True, "preferences": prefs}

        if tool_name == "digest_preferences_get":
            prefs = await asyncio.to_thread(digest_store.get_preferences, user_id=user_id)
            return {"ok": True, "preferences": prefs or {}}

        if tool_name == "digest_generate_now":
            prefs = await asyncio.to_thread(digest_store.get_preferences, user_id=user_id)
            categories = _as_str_list(args.get("categories"))
            if not categories:
                categories = _as_str_list((prefs or {}).get("categories")) or default_categories
            max_items = int(args.get("max_items", default_max_items))
            digest = await digest_service.build_and_store_digest(
                user_id=user_id,
                deployment=reasoning_deployment,
                categories=categories,
                max_items=max(1, min(max_items, 10)),
            )
            await asyncio.to_thread(
                digest_store.track_delivery,
                user_id=user_id,
                digest_id=str(digest.get("digest_id", "")),
                channel="discord_dm",
                status="generated",
            )
            return {"ok": True, "digest": digest}

        if tool_name == "digest_recent_list":
            rows = await asyncio.to_thread(digest_store.list_recent_digests, user_id=user_id, limit=5)
            return {"ok": True, "digests": rows}

        if tool_name == "digest_open":
            digest_id = str(args.get("digest_id", "")).strip()
            if not digest_id:
                return {"ok": False, "error": "Missing digest_id."}
            await asyncio.to_thread(digest_store.track_open, user_id=user_id, digest_id=digest_id, source="discord")
            rate = await asyncio.to_thread(digest_store.digest_open_rate, user_id=user_id, digest_id=digest_id)
            return {"ok": True, "digest_id": digest_id, "open_rate": rate}

        if tool_name == "digest_dig_deeper":
            digest_id = str(args.get("digest_id", "")).strip()
            topic = str(args.get("topic", "")).strip()
            if not digest_id or not topic:
                return {"ok": False, "error": "digest_id and topic are required."}
            return await digest_service.dig_deeper(
                user_id=user_id,
                deployment=reasoning_deployment,
                digest_id=digest_id,
                topic=topic,
            )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": False, "error": f"Unhandled news tool '{tool_name}'."}


def _load_tool_args(arguments_json: str) -> dict[str, Any]:
    raw = (arguments_json or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [tag.strip() for tag in value.split(",") if tag.strip()]
    return []


def _is_valid_time_hhmm(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 2:
        return False
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _is_valid_timezone(value: str) -> bool:
    try:
        ZoneInfo(value)
    except Exception:
        return False
    return True
