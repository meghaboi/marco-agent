from marco_agent.ai.foundry import FoundryChatClient, _extract_content, _extract_tool_calls


def test_foundry_client_uses_openai_v1_mode_for_v1_endpoint() -> None:
    client = FoundryChatClient(
        endpoint="https://example.openai.azure.com/openai/v1",
        key="test-key",
        api_version="2024-10-21",
    )
    assert client._client_mode == "openai_v1_compatible"


def test_foundry_client_uses_azure_mode_for_root_endpoint() -> None:
    client = FoundryChatClient(
        endpoint="https://example.openai.azure.com",
        key="test-key",
        api_version="2024-10-21",
    )
    assert client._client_mode == "azure_deployments"


def test_extract_content_handles_dict_blocks() -> None:
    content = [
        {"type": "output_text", "text": "hello"},
        {"type": "output_text", "text": "world"},
    ]
    assert _extract_content(content) == "hello\nworld"


def test_extract_tool_calls_handles_dict_payloads() -> None:
    message = {
        "tool_calls": [
            {
                "id": "call_123",
                "type": "function",
                "function": {
                    "name": "task_list",
                    "arguments": {"include_closed": False},
                },
            }
        ]
    }
    calls, payloads = _extract_tool_calls(message)
    assert len(calls) == 1
    assert calls[0].id == "call_123"
    assert calls[0].name == "task_list"
    assert calls[0].arguments_json == '{"include_closed": false}'
    assert payloads[0]["function"]["arguments"] == '{"include_closed": false}'


def test_extract_tool_calls_handles_legacy_function_call() -> None:
    message = {
        "function_call": {
            "name": "task_list",
            "arguments": "{}",
        }
    }
    calls, payloads = _extract_tool_calls(message)
    assert len(calls) == 1
    assert calls[0].name == "task_list"
    assert calls[0].arguments_json == "{}"
    assert payloads[0]["type"] == "function"
