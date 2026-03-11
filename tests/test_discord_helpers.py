from marco_agent.discord_bot import _looks_like_textual_tool_stub


def test_detects_textual_tool_stub_marker() -> None:
    text = "tool_call_name\ntask_list\ntool_call_arguments\n{}"
    assert _looks_like_textual_tool_stub(text)


def test_non_stub_text_returns_false() -> None:
    assert not _looks_like_textual_tool_stub("Here are your tasks.")
