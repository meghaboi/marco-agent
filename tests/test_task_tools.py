import asyncio

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
        _ = include_closed
        return [row for row in self.rows if row["user_id"] == user_id]

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
