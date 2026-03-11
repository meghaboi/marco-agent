from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass
from typing import Iterable

import discord
from discord.ext import commands

from marco_agent.ai.foundry import FoundryChatClient
from marco_agent.config import AppFileConfig
from marco_agent.storage.cosmos_memory import CosmosMemoryStore
from marco_agent.storage.cosmos_tasks import CosmosTaskStore

LOGGER = logging.getLogger(__name__)
STRICT_UNAUTHORIZED_MESSAGE = "I only serve meghaboi."


@dataclass(slots=True)
class RuntimeModelState:
    chat: str
    reasoning: str
    embeddings: str


def _chunk_message(text: str, limit: int = 1900) -> Iterable[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + limit])
        start += limit
    return chunks


class MarcoDiscordBot(commands.Bot):
    def __init__(
        self,
        *,
        file_config: AppFileConfig,
        ai_client: FoundryChatClient,
        memory_store: CosmosMemoryStore,
        task_store: CosmosTaskStore,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.file_config = file_config
        self.ai_client = ai_client
        self.memory_store = memory_store
        self.task_store = task_store
        self.model_state = RuntimeModelState(
            chat=file_config.active_models.chat,
            reasoning=file_config.active_models.reasoning,
            embeddings=file_config.active_models.embeddings,
        )

    async def on_ready(self) -> None:
        LOGGER.info("Marco online as %s (%s)", self.user, getattr(self.user, "id", None))

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.guild is not None:
            return

        author_id = str(message.author.id)
        authorized_id = self.file_config.security.authorized_discord_user_id
        if author_id != authorized_id:
            await message.channel.send(STRICT_UNAUTHORIZED_MESSAGE)
            await asyncio.to_thread(
                self.memory_store.save_unauthorized_attempt,
                user_id=author_id,
                content=message.content,
            )
            LOGGER.warning("Unauthorized DM blocked from user %s", author_id)
            return

        content = (message.content or "").strip()
        if not content:
            await message.channel.send("Send a message or command, and I will handle it.")
            return

        lowered = content.lower()
        if lowered == "model list":
            await self._handle_model_list(message)
            return
        if lowered.startswith("model use"):
            await self._handle_model_use(message, content)
            return
        if lowered.startswith("add task "):
            await self._handle_add_task(message, content)
            return
        if lowered == "show tasks":
            await self._handle_show_tasks(message)
            return
        if lowered.startswith("complete task "):
            await self._handle_complete_task(message, content)
            return
        if lowered.startswith("delete task "):
            await self._handle_delete_task(message, content)
            return

        await self._respond_as_marco(message)

    async def _handle_model_list(self, message: discord.Message) -> None:
        profile_map = self.file_config.profile_map()
        rows = ["Available model profiles:"]
        for profile in self.file_config.model_profiles:
            rows.append(
                f"- `{profile.id}` -> deployment `{profile.azure_deployment}` ({profile.description})"
            )
        rows.append("")
        rows.append("Active routing:")
        rows.append(f"- chat: `{self.model_state.chat}` -> `{profile_map[self.model_state.chat].azure_deployment}`")
        rows.append(
            f"- reasoning: `{self.model_state.reasoning}` -> `{profile_map[self.model_state.reasoning].azure_deployment}`"
        )
        rows.append(
            f"- embeddings: `{self.model_state.embeddings}` -> `{profile_map[self.model_state.embeddings].azure_deployment}`"
        )
        await message.channel.send("\n".join(rows))

    async def _handle_model_use(self, message: discord.Message, raw: str) -> None:
        if not self.file_config.assistant.allow_runtime_model_switch:
            await message.channel.send("Runtime model switching is disabled by config.")
            return

        parts = raw.split()
        if len(parts) != 4:
            await message.channel.send("Usage: `model use <chat|reasoning|embeddings> <profile_id>`")
            return

        _, _, capability, profile_id = parts
        capability = capability.lower()
        if capability not in {"chat", "reasoning", "embeddings"}:
            await message.channel.send("Capability must be one of: chat, reasoning, embeddings.")
            return

        profile_map = self.file_config.profile_map()
        if profile_id not in profile_map:
            await message.channel.send(f"Unknown profile id: `{profile_id}`.")
            return

        setattr(self.model_state, capability, profile_id)
        deployment = profile_map[profile_id].azure_deployment
        await message.channel.send(
            f"Updated `{capability}` model to profile `{profile_id}` (deployment `{deployment}`)."
        )

    async def _respond_as_marco(self, message: discord.Message) -> None:
        user_id = str(message.author.id)
        recent = await asyncio.to_thread(
            self.memory_store.load_recent_messages,
            user_id=user_id,
            limit=self.file_config.assistant.max_memory_messages,
        )
        memory_block = self._render_memory_block(recent)
        system_prompt = (
            f"{self.file_config.persona.seed_prompt}\n\n"
            f"Current context about {self.file_config.security.principal_name}:\n{memory_block}"
        )

        await asyncio.to_thread(
            self.memory_store.save_message,
            user_id=user_id,
            role="user",
            content=message.content,
        )

        deployment = self.file_config.profile_map()[self.model_state.chat].azure_deployment
        response = await self.ai_client.chat(
            deployment=deployment,
            system_prompt=system_prompt,
            user_prompt=message.content,
            temperature=self.file_config.assistant.default_temperature,
        )

        await asyncio.to_thread(
            self.memory_store.save_message,
            user_id=user_id,
            role="assistant",
            content=response,
        )

        for chunk in _chunk_message(response):
            await message.channel.send(chunk)

    async def _handle_add_task(self, message: discord.Message, raw: str) -> None:
        if not self.task_store.enabled:
            await message.channel.send("Task store is not configured yet. Set Cosmos credentials first.")
            return
        parsed = _parse_add_task_command(raw)
        if parsed is None:
            await message.channel.send(
                "Usage: `add task <title> [--priority P0|P1|P2|P3] [--due YYYY-MM-DD] [--tags t1,t2]`"
            )
            return

        title, priority, due_at, tags = parsed
        user_id = str(message.author.id)
        item = await asyncio.to_thread(
            self.task_store.add_task,
            user_id=user_id,
            title=title,
            priority=priority,
            due_at=due_at,
            tags=tags,
        )
        await message.channel.send(
            f"Task added: `{item['id']}` | `{item['priority']}` | {item['title']}"
            + (f" | due `{item['due_at']}`" if item.get("due_at") else "")
        )

    async def _handle_show_tasks(self, message: discord.Message) -> None:
        user_id = str(message.author.id)
        tasks = await asyncio.to_thread(self.task_store.list_tasks, user_id=user_id, include_closed=False)
        if not tasks:
            await message.channel.send("No open tasks.")
            return
        lines = ["Open tasks:"]
        for item in tasks[:25]:
            due = item.get("due_at") or "no due date"
            lines.append(f"- `{item['id']}` [{item['priority']}] {item['title']} (due: {due})")
        await message.channel.send("\n".join(lines))

    async def _handle_complete_task(self, message: discord.Message, raw: str) -> None:
        user_id = str(message.author.id)
        task_id = raw.replace("complete task", "", 1).strip()
        if not task_id:
            await message.channel.send("Usage: `complete task <task_id>`")
            return
        ok = await asyncio.to_thread(self.task_store.complete_task, user_id=user_id, task_id=task_id)
        if ok:
            await message.channel.send(f"Completed task `{task_id}`.")
        else:
            await message.channel.send(f"Task `{task_id}` not found.")

    async def _handle_delete_task(self, message: discord.Message, raw: str) -> None:
        user_id = str(message.author.id)
        task_id = raw.replace("delete task", "", 1).strip()
        if not task_id:
            await message.channel.send("Usage: `delete task <task_id>`")
            return
        ok = await asyncio.to_thread(self.task_store.delete_task, user_id=user_id, task_id=task_id)
        if ok:
            await message.channel.send(f"Deleted task `{task_id}`.")
        else:
            await message.channel.send(f"Task `{task_id}` not found.")

    @staticmethod
    def _render_memory_block(entries: list[dict]) -> str:
        if not entries:
            return "No prior conversation memory."
        lines = []
        for item in entries:
            role = item.get("role", "unknown")
            content = item.get("content", "").strip()
            if not content:
                continue
            lines.append(f"{role}: {content}")
        return "\n".join(lines) if lines else "No prior conversation memory."


def _parse_add_task_command(raw: str) -> tuple[str, str, str | None, list[str]] | None:
    try:
        parts = shlex.split(raw)
    except ValueError:
        return None

    if len(parts) < 3 or parts[0].lower() != "add" or parts[1].lower() != "task":
        return None

    title_tokens: list[str] = []
    priority = "P2"
    due_at: str | None = None
    tags: list[str] = []
    idx = 2
    while idx < len(parts):
        token = parts[idx]
        if token == "--priority" and idx + 1 < len(parts):
            priority = parts[idx + 1].upper()
            idx += 2
            continue
        if token == "--due" and idx + 1 < len(parts):
            due_at = parts[idx + 1]
            idx += 2
            continue
        if token == "--tags" and idx + 1 < len(parts):
            tags = [tag.strip() for tag in parts[idx + 1].split(",") if tag.strip()]
            idx += 2
            continue
        title_tokens.append(token)
        idx += 1

    title = " ".join(title_tokens).strip()
    if not title:
        return None
    return (title, priority, due_at, tags)
