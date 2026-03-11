from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable

import discord
from discord.ext import commands

from marco_agent.ai.foundry import FoundryChatClient, ToolCallRequest
from marco_agent.config import AppFileConfig
from marco_agent.storage.cosmos_memory import CosmosMemoryStore
from marco_agent.storage.cosmos_tasks import CosmosTaskStore
from marco_agent.tools.task_tools import TASK_TOOL_NAMES, execute_task_tool_call, task_tool_definitions

LOGGER = logging.getLogger(__name__)
STRICT_UNAUTHORIZED_MESSAGE = "I only serve meghaboi."
MAX_TOOL_CALL_ROUNDS = 4


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
        user_text = message.content.strip()
        recent = await asyncio.to_thread(
            self.memory_store.load_recent_messages,
            user_id=user_id,
            limit=self.file_config.assistant.max_memory_messages,
        )

        system_prompt = self._build_system_prompt()
        messages = self._build_messages_for_model(system_prompt=system_prompt, recent=recent, user_text=user_text)

        await asyncio.to_thread(
            self.memory_store.save_message,
            user_id=user_id,
            role="user",
            content=user_text,
        )

        # Tool orchestration runs on the reasoning profile, which should be tool-call capable.
        deployment = self.file_config.profile_map()[self.model_state.reasoning].azure_deployment
        response_text = await self._run_tool_loop(
            user_id=user_id,
            deployment=deployment,
            messages=messages,
        )

        await asyncio.to_thread(
            self.memory_store.save_message,
            user_id=user_id,
            role="assistant",
            content=response_text,
        )

        for chunk in _chunk_message(response_text):
            await message.channel.send(chunk)

    def _build_system_prompt(self) -> str:
        return (
            f"{self.file_config.persona.seed_prompt}\n\n"
            "Tool-use policy:\n"
            "- For any task-related action (add/list/show/update/complete/delete/reprioritize), you MUST call task tools.\n"
            "- Never fabricate task lists, task IDs, statuses, or due dates.\n"
            "- If task tool output says unavailable/error, tell the user plainly and do not invent success.\n"
            "- After tool calls, provide a concise grounded response based only on tool outputs."
        )

    @staticmethod
    def _build_messages_for_model(
        *,
        system_prompt: str,
        recent: list[dict[str, Any]],
        user_text: str,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for item in recent:
            role = str(item.get("role", "")).strip().lower()
            content = str(item.get("content", "")).strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_text})
        return messages

    async def _run_tool_loop(
        self,
        *,
        user_id: str,
        deployment: str,
        messages: list[dict[str, Any]],
    ) -> str:
        tools = task_tool_definitions()
        work_messages = list(messages)

        for _ in range(MAX_TOOL_CALL_ROUNDS):
            result = await self.ai_client.complete_messages(
                deployment=deployment,
                messages=work_messages,
                temperature=self.file_config.assistant.default_temperature,
                tools=tools,
                tool_choice="auto",
            )
            if not result.tool_calls:
                if _looks_like_textual_tool_stub(result.content):
                    return (
                        "Tool execution is blocked: active orchestration model returned textual tool markers "
                        "instead of structured tool calls. Set `active_models.reasoning` to a tool-capable "
                        "deployment (for example `Kimi-K2.5`)."
                    )
                if result.content:
                    return result.content
                return "No content generated. Try rephrasing your request."

            work_messages.append(result.assistant_message)
            for call in result.tool_calls:
                tool_output = await self._execute_tool_call(user_id=user_id, call=call)
                work_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(tool_output, ensure_ascii=True),
                    }
                )

        return "I hit an internal tool-call loop limit. Please retry your request."

    async def _execute_tool_call(self, *, user_id: str, call: ToolCallRequest) -> dict[str, Any]:
        LOGGER.info("Tool call requested: %s(%s)", call.name, call.arguments_json)
        if call.name in TASK_TOOL_NAMES:
            return await execute_task_tool_call(
                task_store=self.task_store,
                user_id=user_id,
                tool_name=call.name,
                arguments_json=call.arguments_json,
            )
        return {"ok": False, "error": f"Unknown tool '{call.name}'."}


def _looks_like_textual_tool_stub(content: str) -> bool:
    text = (content or "").lower()
    return "tool_call_name" in text and "tool_call_arguments" in text
