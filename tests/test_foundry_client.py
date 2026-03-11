from marco_agent.ai.foundry import FoundryChatClient


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
