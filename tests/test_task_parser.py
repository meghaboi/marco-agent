from marco_agent.discord_bot import _parse_add_task_command


def test_parse_add_task_command() -> None:
    raw = "add task Build parser --priority P1 --due 2026-03-12 --tags ai,backend"
    result = _parse_add_task_command(raw)
    assert result is not None
    title, priority, due_at, tags = result
    assert title == "Build parser"
    assert priority == "P1"
    assert due_at == "2026-03-12"
    assert tags == ["ai", "backend"]


def test_parse_add_task_command_requires_title() -> None:
    assert _parse_add_task_command("add task --priority P2") is None
