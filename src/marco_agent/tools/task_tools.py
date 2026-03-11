from __future__ import annotations

import asyncio
import json
from typing import Any

from marco_agent.storage.cosmos_tasks import CosmosTaskStore

TASK_TOOL_NAMES = {"task_add", "task_list", "task_complete", "task_delete"}


def task_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "task_add",
                "description": "Create a new task for the principal.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Short task title."},
                        "description": {"type": "string", "description": "Optional details."},
                        "priority": {
                            "type": "string",
                            "enum": ["P0", "P1", "P2", "P3"],
                            "description": "P0 is highest urgency.",
                        },
                        "due_at": {
                            "type": "string",
                            "description": "Optional due date in YYYY-MM-DD or ISO datetime.",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional tag list.",
                        },
                        "notes": {"type": "string", "description": "Optional operator notes."},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "task_list",
                "description": "List tasks currently tracked for the principal.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "include_closed": {
                            "type": "boolean",
                            "description": "Set true to include completed tasks.",
                        }
                    },
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "task_complete",
                "description": "Mark a task as completed by task ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task identifier."}
                    },
                    "required": ["task_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "task_delete",
                "description": "Delete a task by task ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task identifier."}
                    },
                    "required": ["task_id"],
                    "additionalProperties": False,
                },
            },
        },
    ]


async def execute_task_tool_call(
    *,
    task_store: CosmosTaskStore,
    user_id: str,
    tool_name: str,
    arguments_json: str,
) -> dict[str, Any]:
    args = _load_tool_args(arguments_json)
    if tool_name not in TASK_TOOL_NAMES:
        return {"ok": False, "error": f"Unknown task tool '{tool_name}'."}
    if not task_store.enabled:
        return {"ok": False, "error": "Task store is unavailable. Configure Cosmos DB."}

    try:
        if tool_name == "task_add":
            item = await asyncio.to_thread(
                task_store.add_task,
                user_id=user_id,
                title=str(args.get("title", "")).strip(),
                description=str(args.get("description", "")).strip(),
                priority=str(args.get("priority", "P2")).upper().strip(),
                due_at=_as_optional_str(args.get("due_at")),
                tags=_as_str_list(args.get("tags")),
                notes=str(args.get("notes", "")).strip(),
            )
            return {"ok": True, "task": item}

        if tool_name == "task_list":
            tasks = await asyncio.to_thread(
                task_store.list_tasks,
                user_id=user_id,
                include_closed=bool(args.get("include_closed", False)),
            )
            return {"ok": True, "tasks": tasks, "count": len(tasks)}

        if tool_name == "task_complete":
            task_id = str(args.get("task_id", "")).strip()
            if not task_id:
                return {"ok": False, "error": "Missing required field: task_id."}
            done = await asyncio.to_thread(task_store.complete_task, user_id=user_id, task_id=task_id)
            return {"ok": bool(done), "task_id": task_id}

        if tool_name == "task_delete":
            task_id = str(args.get("task_id", "")).strip()
            if not task_id:
                return {"ok": False, "error": "Missing required field: task_id."}
            deleted = await asyncio.to_thread(task_store.delete_task, user_id=user_id, task_id=task_id)
            return {"ok": bool(deleted), "task_id": task_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    return {"ok": False, "error": f"Unhandled task tool '{tool_name}'."}


def _load_tool_args(arguments_json: str) -> dict[str, Any]:
    raw = (arguments_json or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [tag.strip() for tag in value.split(",") if tag.strip()]
    return []
