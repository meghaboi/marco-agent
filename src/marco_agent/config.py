from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError


DEFAULT_CONFIG_PATH = Path("config/marco.config.yaml")


class SecurityConfig(BaseModel):
    principal_name: str
    principal_discord_username: str
    authorized_discord_user_id: str
    unauthorized_message: str = "I only serve meghaboi."


class AssistantConfig(BaseModel):
    name: str = "Marco"
    allow_runtime_model_switch: bool = True
    max_memory_messages: int = 20
    max_semantic_memory_messages: int = 8
    semantic_memory_enabled: bool = True
    default_temperature: float = 0.25


class CodexExecutionConfig(BaseModel):
    enabled: bool = False
    auth_mode: str = "interactive_login"
    session_ttl_minutes: int = 120


class ExecutionConfig(BaseModel):
    codex: CodexExecutionConfig = Field(default_factory=CodexExecutionConfig)


class DigestDefaultsConfig(BaseModel):
    default_time_local: str = "08:30"
    default_timezone: str = "UTC"
    default_categories: list[str] = Field(
        default_factory=lambda: ["geopolitics", "ai", "ml", "game development", "general"]
    )
    max_items: int = 5


class RetrievalConfig(BaseModel):
    semantic_similarity_threshold: float = 0.65


class ModelProfile(BaseModel):
    id: str
    description: str
    azure_deployment: str


class ActiveModels(BaseModel):
    chat: str
    reasoning: str
    embeddings: str


class PersonaConfig(BaseModel):
    seed_prompt: str


class AppFileConfig(BaseModel):
    security: SecurityConfig
    assistant: AssistantConfig
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    digest: DigestDefaultsConfig = Field(default_factory=DigestDefaultsConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    model_profiles: list[ModelProfile]
    active_models: ActiveModels
    persona: PersonaConfig

    def profile_map(self) -> dict[str, ModelProfile]:
        return {profile.id: profile for profile in self.model_profiles}

    def get_deployment_for_capability(self, capability: str) -> str:
        model_id = getattr(self.active_models, capability)
        profile = self.profile_map().get(model_id)
        if profile is None:
            raise ValueError(f"Active model '{model_id}' for capability '{capability}' not found.")
        return profile.azure_deployment


class EnvConfig(BaseModel):
    discord_bot_token: str | None = Field(default=None, alias="DISCORD_BOT_TOKEN")
    discord_guild_id: str | None = Field(default=None, alias="DISCORD_GUILD_ID")
    azure_ai_foundry_endpoint: str = Field(alias="AZURE_AI_FOUNDRY_ENDPOINT")
    azure_ai_foundry_key: str = Field(alias="AZURE_AI_FOUNDRY_KEY")
    azure_ai_foundry_api_version: str = Field(default="2024-10-21", alias="AZURE_AI_FOUNDRY_API_VERSION")
    cosmos_db_endpoint: str | None = Field(default=None, alias="COSMOS_DB_ENDPOINT")
    cosmos_db_key: str | None = Field(default=None, alias="COSMOS_DB_KEY")
    cosmos_db_database: str = Field(default="marco", alias="COSMOS_DB_DATABASE")
    cosmos_db_container: str = Field(default="conversation_memory", alias="COSMOS_DB_CONTAINER")
    cosmos_tasks_container: str = Field(default="tasks", alias="COSMOS_TASKS_CONTAINER")
    cosmos_digest_container: str = Field(default="news_digest", alias="COSMOS_DIGEST_CONTAINER")
    appinsights_connection_string: str | None = Field(
        default=None,
        alias="APPLICATIONINSIGHTS_CONNECTION_STRING",
    )
    news_rss_url: str = Field(
        default=(
            "https://news.google.com/rss/search?"
            "q={query}&hl=en-US&gl=US&ceid=US:en"
        ),
        alias="NEWS_RSS_URL_TEMPLATE",
    )
    digest_timer_schedule: str = Field(default="0 30 2 * * *", alias="DIGEST_TIMER_SCHEDULE")
    digest_open_tracking_base_url: str | None = Field(default=None, alias="DIGEST_OPEN_TRACKING_BASE_URL")
    port: int = Field(default=8080, alias="PORT")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file must deserialize to a dict: {path}")
    return data


def load_file_config(config_path: Path = DEFAULT_CONFIG_PATH) -> AppFileConfig:
    data = _load_yaml(config_path)
    try:
        cfg = AppFileConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid config in {config_path}: {exc}") from exc

    profile_map = cfg.profile_map()
    if len(profile_map) != len(cfg.model_profiles):
        raise ValueError("Duplicate model profile IDs found in config.")

    for capability in ("chat", "reasoning", "embeddings"):
        model_id = getattr(cfg.active_models, capability)
        if model_id not in profile_map:
            raise ValueError(
                f"Capability '{capability}' points to unknown profile '{model_id}'."
            )

    return cfg


def load_env_config() -> EnvConfig:
    payload: dict[str, Any] = {}
    for field_name, field_info in EnvConfig.model_fields.items():
        _ = field_name
        alias = field_info.alias
        if alias is None:
            continue
        value = os.environ.get(alias)
        if value is not None:
            payload[alias] = value

    try:
        return EnvConfig.model_validate(payload)
    except ValidationError as exc:
        missing = [err["loc"][0] for err in exc.errors()]
        raise ValueError(f"Missing or invalid environment configuration: {missing}") from exc
