import asyncio
from datetime import UTC, datetime, timedelta

from marco_agent.tools.task_tools import (
    TASK_TOOL_NAMES,
    execute_task_tool_call,
    task_tool_definitions,
)


class StubTaskStore:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.rows = []

    def add_task(self, *, user_id, title, description="", priority="P2", due_at=None, tags=None, notes=""):
        item = {
            "id": "abc12345",
            "user_id": user_id,
            "title": title,
            "description": description,
            "priority": priority,
            "due_at": due_at,
            "tags": tags or [],
            "notes": notes,
            "status": "open",
        }
        self.rows.append(item)
        return item

    def list_tasks(self, *, user_id, include_closed=False):
        rows = [row for row in self.rows if row["user_id"] == user_id]
        if include_closed:
            return rows
        return [row for row in rows if row["status"] != "done"]

    def complete_task(self, *, user_id, task_id):
        for row in self.rows:
            if row["user_id"] == user_id and row["id"] == task_id:
                row["status"] = "done"
                return True
        return False

    def delete_task(self, *, user_id, task_id):
        before = len(self.rows)
        self.rows = [row for row in self.rows if not (row["user_id"] == user_id and row["id"] == task_id)]
        return len(self.rows) < before


def test_task_tool_definitions_include_expected_tools() -> None:
    definitions = task_tool_definitions()
    names = {item["function"]["name"] for item in definitions}
    assert names == TASK_TOOL_NAMES


def test_execute_task_add_and_list() -> None:
    store = StubTaskStore(enabled=True)
    add_result = asyncio.run(
        execute_task_tool_call(
            task_store=store,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="task_add",
            arguments_json='{"title":"Ship v1","priority":"P1","tags":["marco","launch"]}',
        )
    )
    assert add_result["ok"] is True
    assert add_result["task"]["title"] == "Ship v1"

    list_result = asyncio.run(
        execute_task_tool_call(
            task_store=store,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="task_list",
            arguments_json="{}",
        )
    )
    assert list_result["ok"] is True
    assert list_result["count"] == 1


def test_execute_task_tool_when_store_disabled() -> None:
    store = StubTaskStore(enabled=False)
    result = asyncio.run(
        execute_task_tool_call(
            task_store=store,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="task_list",
            arguments_json="{}",
        )
    )
    assert result["ok"] is False
    assert "unavailable" in result["error"].lower()


def test_execute_unknown_task_tool_returns_error() -> None:
    store = StubTaskStore(enabled=True)
    result = asyncio.run(
        execute_task_tool_call(
            task_store=store,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="unknown_tool",
            arguments_json="{}",
        )
    )
    assert result["ok"] is False
    assert "unknown task tool" in result["error"].lower()


def test_execute_complete_and_delete_regression_paths() -> None:
    store = StubTaskStore(enabled=True)
    asyncio.run(
        execute_task_tool_call(
            task_store=store,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="task_add",
            arguments_json='{"title":"Ship v1"}',
        )
    )

    complete_ok = asyncio.run(
        execute_task_tool_call(
            task_store=store,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="task_complete",
            arguments_json='{"task_id":"abc12345"}',
        )
    )
    assert complete_ok["ok"] is True

    complete_miss = asyncio.run(
        execute_task_tool_call(
            task_store=store,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="task_complete",
            arguments_json='{"task_id":"missing"}',
        )
    )
    assert complete_miss["ok"] is False

    delete_ok = asyncio.run(
        execute_task_tool_call(
            task_store=store,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="task_delete",
            arguments_json='{"task_id":"abc12345"}',
        )
    )
    assert delete_ok["ok"] is True

    delete_miss = asyncio.run(
        execute_task_tool_call(
            task_store=store,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="task_delete",
            arguments_json='{"task_id":"missing"}',
        )
    )
    assert delete_miss["ok"] is False


def test_execute_task_actions_validate_missing_task_id() -> None:
    store = StubTaskStore(enabled=True)
    complete_result = asyncio.run(
        execute_task_tool_call(
            task_store=store,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="task_complete",
            arguments_json="{}",
        )
    )
    assert complete_result["ok"] is False
    assert "missing required field" in complete_result["error"].lower()

    delete_result = asyncio.run(
        execute_task_tool_call(
            task_store=store,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="task_delete",
            arguments_json="{}",
        )
    )
    assert delete_result["ok"] is False
    assert "missing required field" in delete_result["error"].lower()


def test_execute_task_morning_summary_detects_overdue() -> None:
    store = StubTaskStore(enabled=True)
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    store.rows.extend(
        [
            {
                "id": "a1",
                "user_id": "u1",
                "title": "Late task",
                "priority": "P1",
                "due_at": yesterday,
                "status": "open",
            },
            {
                "id": "a2",
                "user_id": "u1",
                "title": "Future task",
                "priority": "P2",
                "due_at": tomorrow,
                "status": "open",
            },
        ]
    )
    result = asyncio.run(
        execute_task_tool_call(
            task_store=store,  # type: ignore[arg-type]
            user_id="u1",
            tool_name="task_morning_summary",
            arguments_json='{"timezone":"UTC"}',
        )
    )
    assert result["ok"] is True
    assert result["totals"]["open"] == 2
    assert result["totals"]["overdue"] == 1
    assert result["overdue_tasks"][0]["id"] == "a1"
