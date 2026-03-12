import discord

from marco_agent.discord_bot import (
    _build_task_action_embed,
    _build_task_add_embed,
    _build_task_list_embed,
    _is_authorized_user,
    _looks_like_textual_tool_stub,
)


def test_detects_textual_tool_stub_marker() -> None:
    text = "tool_call_name\ntask_list\ntool_call_arguments\n{}"
    assert _looks_like_textual_tool_stub(text)


def test_non_stub_text_returns_false() -> None:
    assert not _looks_like_textual_tool_stub("Here are your tasks.")


def test_is_authorized_user_strict_id_match() -> None:
    assert _is_authorized_user(author_id="123", authorized_id="123")
    assert not _is_authorized_user(author_id="123", authorized_id="456")


def test_task_list_embed_success() -> None:
    embed = _build_task_list_embed(
        {
            "ok": True,
            "count": 1,
            "tasks": [
                {
                    "id": "abc12345",
                    "title": "Ship v1",
                    "priority": "P1",
                    "status": "open",
                    "due_at": "2026-03-20",
                }
            ],
        }
    )
    assert embed.title == "Task Board"
    assert len(embed.fields) == 1
    assert "abc12345" in embed.fields[0].value


def test_task_add_embed_success() -> None:
    embed = _build_task_add_embed(
        {
            "ok": True,
            "task": {
                "id": "abc12345",
                "title": "Ship v1",
                "priority": "P1",
                "description": "release candidate",
                "due_at": "2026-03-20",
                "tags": ["launch", "prod"],
            },
        }
    )
    assert embed.title == "Task Created"
    assert embed.description == "Ship v1"
    assert any(field.name == "Task ID" and "abc12345" in field.value for field in embed.fields)


def test_task_action_embed_failure() -> None:
    embed = _build_task_action_embed(
        {"ok": False, "task_id": "abc12345", "error": "Not found"},
        success_title="Task Completed",
        success_color=discord.Color.green(),
        failure_title="Task Completion Failed",
        failure_color=discord.Color.red(),
    )
    assert embed.title == "Task Completion Failed"
    assert "abc12345" in (embed.description or "")
    assert any(field.name == "Error" and "Not found" in field.value for field in embed.fields)
