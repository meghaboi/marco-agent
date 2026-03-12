from pathlib import Path

import pytest

from marco_agent.config import load_file_config


def test_load_file_config_smoke() -> None:
    cfg = load_file_config(Path("config/marco.config.yaml"))
    assert cfg.security.unauthorized_message == "I only serve meghaboi."
    assert cfg.get_deployment_for_capability("chat")
    assert cfg.active_models.chat == "kimi-k2.5"
    assert cfg.active_models.reasoning == "kimi-k2.5"
    assert cfg.assistant.allow_runtime_model_switch is False
    assert cfg.get_deployment_for_capability("reasoning") == "Kimi-K2.5"


def test_missing_profile_raises(tmp_path: Path) -> None:
    config_file = tmp_path / "bad.yaml"
    config_file.write_text(
        """
security:
  principal_name: test
  principal_discord_username: test
  authorized_discord_user_id: "1"
assistant:
  name: Marco
model_profiles:
  - id: a
    description: x
    azure_deployment: gpt-4o
active_models:
  chat: not_here
  reasoning: a
  embeddings: a
persona:
  seed_prompt: hello
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_file_config(config_file)
