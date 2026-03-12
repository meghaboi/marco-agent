from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable

import discord
from discord.ext import commands

from marco_agent.ai.foundry import FoundryChatClient, ToolCallRequest
from marco_agent.config import AppFileConfig
from marco_agent.observability import correlation_scope, new_correlation_id
from marco_agent.services.memory_retrieval import MemoryRetrievalService
from marco_agent.services.news_digest import NewsDigestService
from marco_agent.storage.cosmos_digest import CosmosDigestStore
from marco_agent.storage.cosmos_memory import CosmosMemoryStore
from marco_agent.storage.cosmos_tasks import CosmosTaskStore
from marco_agent.tools.news_tools import NEWS_TOOL_NAMES, execute_news_tool_call, news_tool_definitions
from marco_agent.tools.task_tools import TASK_TOOL_NAMES, execute_task_tool_call, task_tool_definitions

LOGGER = logging.getLogger(__name__)
STRICT_UNAUTHORIZED_MESSAGE = "I only serve meghaboi."
MAX_TOOL_CALL_ROUNDS = 4


@dataclass(slots=True)
class RuntimeModelState:
    chat: str
    reasoning: str
    embeddings: str


@dataclass(slots=True)
class BotReply:
    text: str
    embeds: list[discord.Embed]


@dataclass(slots=True)
class ToolEvent:
    name: str
    output: dict[str, Any]


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
        digest_store: CosmosDigestStore,
        memory_retrieval: MemoryRetrievalService,
        news_digest_service: NewsDigestService,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.file_config = file_config
        self.ai_client = ai_client
        self.memory_store = memory_store
        self.task_store = task_store
        self.digest_store = digest_store
        self.memory_retrieval = memory_retrieval
        self.news_digest_service = news_digest_service
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
        corr = new_correlation_id(prefix="dm")
        with correlation_scope(value=corr):
            await self._on_message_scoped(message)

    async def _on_message_scoped(self, message: discord.Message) -> None:
        LOGGER.info("Inbound DM received from user_id=%s", getattr(message.author, "id", None))

        author_id = str(message.author.id)
        authorized_id = self.file_config.security.authorized_discord_user_id
        if not _is_authorized_user(author_id=author_id, authorized_id=authorized_id):
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

        async with message.channel.typing():
            await self._respond_as_marco(message)

    async def _handle_model_list(self, message: discord.Message) -> None:
        profile_map = self.file_config.profile_map()
        embed = discord.Embed(
            title="Model Profiles",
            description="Runtime routing and deployment mapping.",
            color=discord.Color.blue(),
        )
        rows = []
        for profile in self.file_config.model_profiles:
            rows.append(
                f"- `{profile.id}` -> deployment `{profile.azure_deployment}` ({profile.description})"
            )
        embed.add_field(name="Available", value="\n".join(rows)[:1024] or "-", inline=False)
        active = (
            f"chat: `{self.model_state.chat}` -> `{profile_map[self.model_state.chat].azure_deployment}`\n"
            f"reasoning: `{self.model_state.reasoning}` -> `{profile_map[self.model_state.reasoning].azure_deployment}`\n"
            f"embeddings: `{self.model_state.embeddings}` -> `{profile_map[self.model_state.embeddings].azure_deployment}`"
        )
        embed.add_field(name="Active Routing", value=active[:1024] or "-", inline=False)
        await message.channel.send(embed=embed)

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
        embeddings_deployment = self.file_config.profile_map()[self.model_state.embeddings].azure_deployment
        retrieved = await self.memory_retrieval.retrieve_context(
            user_id=user_id,
            user_text=user_text,
            embeddings_deployment=embeddings_deployment,
        )

        system_prompt = self._build_system_prompt()
        messages = self._build_messages_for_model(system_prompt=system_prompt, recent=retrieved, user_text=user_text)

        await asyncio.to_thread(
            self.memory_store.save_message,
            user_id=user_id,
            role="user",
            content=user_text,
        )
        await self._index_message_embedding(
            user_id=user_id,
            role="user",
            content=user_text,
            embeddings_deployment=embeddings_deployment,
        )

        # Tool orchestration runs on the reasoning profile, which should be tool-call capable.
        deployment = self.file_config.profile_map()[self.model_state.reasoning].azure_deployment
        reply = await self._run_tool_loop(
            user_id=user_id,
            deployment=deployment,
            messages=messages,
        )

        await asyncio.to_thread(
            self.memory_store.save_message,
            user_id=user_id,
            role="assistant",
            content=reply.text,
        )
        await self._index_message_embedding(
            user_id=user_id,
            role="assistant",
            content=reply.text,
            embeddings_deployment=embeddings_deployment,
        )

        if reply.text:
            for chunk in _chunk_message(reply.text):
                await message.channel.send(chunk)
        for embed in reply.embeds:
            await message.channel.send(embed=embed)

    async def _index_message_embedding(
        self,
        *,
        user_id: str,
        role: str,
        content: str,
        embeddings_deployment: str,
    ) -> None:
        text = content.strip()
        if not text or not self.memory_store.enabled:
            return
        vectors = await self.ai_client.embed_texts(
            deployment=embeddings_deployment,
            texts=[text],
        )
        if not vectors:
            return
        await asyncio.to_thread(
            self.memory_store.save_message_embedding,
            user_id=user_id,
            role=role,
            content=text,
            embedding=vectors[0],
        )

    def _build_system_prompt(self) -> str:
        return (
            f"{self.file_config.persona.seed_prompt}\n\n"
            "Tool-use policy:\n"
            "- For any task-related action (add/list/show/update/complete/delete/reprioritize), you MUST call task tools.\n"
            "- For digest operations (preferences, generate digest, open rate, dig deeper), you MUST call digest tools.\n"
            "- Never fabricate task lists, task IDs, statuses, or due dates.\n"
            "- Never fabricate news facts or sources. Use only grounded source URLs returned by tools.\n"
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
    ) -> BotReply:
        tools = task_tool_definitions() + news_tool_definitions()
        work_messages = list(messages)
        tool_events: list[ToolEvent] = []

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
                    return BotReply(
                        text=(
                            "Tool execution is blocked: active orchestration model returned textual tool markers "
                            "instead of structured tool calls. Set `active_models.reasoning` to a tool-capable "
                            "deployment (for example `Kimi-K2.5`)."
                        ),
                        embeds=[],
                    )
                if result.content:
                    return BotReply(text=result.content, embeds=self._build_task_embeds(tool_events))
                return BotReply(
                    text="No content generated. Try rephrasing your request.",
                    embeds=self._build_task_embeds(tool_events),
                )

            work_messages.append(result.assistant_message)
            for call in result.tool_calls:
                tool_output = await self._execute_tool_call(
                    user_id=user_id,
                    call=call,
                    reasoning_deployment=deployment,
                )
                tool_events.append(ToolEvent(name=call.name, output=tool_output))
                work_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(tool_output, ensure_ascii=True),
                    }
                )

        return BotReply(
            text="I hit an internal tool-call loop limit. Please retry your request.",
            embeds=self._build_task_embeds(tool_events),
        )

    async def _execute_tool_call(
        self,
        *,
        user_id: str,
        call: ToolCallRequest,
        reasoning_deployment: str,
    ) -> dict[str, Any]:
        LOGGER.info("Tool call requested: %s(%s)", call.name, call.arguments_json)
        if call.name in TASK_TOOL_NAMES:
            return await execute_task_tool_call(
                task_store=self.task_store,
                user_id=user_id,
                tool_name=call.name,
                arguments_json=call.arguments_json,
            )
        if call.name in NEWS_TOOL_NAMES:
            return await execute_news_tool_call(
                digest_store=self.digest_store,
                digest_service=self.news_digest_service,
                user_id=user_id,
                tool_name=call.name,
                arguments_json=call.arguments_json,
                default_categories=self.file_config.digest.default_categories,
                default_max_items=self.file_config.digest.max_items,
                reasoning_deployment=reasoning_deployment,
            )
        return {"ok": False, "error": f"Unknown tool '{call.name}'."}

    @staticmethod
    def _build_task_embeds(tool_events: list[ToolEvent]) -> list[discord.Embed]:
        if not tool_events:
            return []

        embeds: list[discord.Embed] = []
        for event in tool_events:
            if event.name == "task_list":
                embed = _build_task_list_embed(event.output)
            elif event.name == "task_add":
                embed = _build_task_add_embed(event.output)
            elif event.name == "task_complete":
                embed = _build_task_action_embed(
                    event.output,
                    success_title="Task Completed",
                    success_color=discord.Color.green(),
                    failure_title="Task Completion Failed",
                    failure_color=discord.Color.red(),
                )
            elif event.name == "task_delete":
                embed = _build_task_action_embed(
                    event.output,
                    success_title="Task Deleted",
                    success_color=discord.Color.orange(),
                    failure_title="Task Delete Failed",
                    failure_color=discord.Color.red(),
                )
            elif event.name in {"digest_preferences_set", "digest_preferences_get"}:
                embed = _build_digest_preferences_embed(event.output)
            elif event.name == "digest_generate_now":
                embed = _build_digest_embed(event.output)
            elif event.name == "digest_recent_list":
                embed = _build_digest_recent_embed(event.output)
            elif event.name == "digest_open":
                embed = _build_digest_open_rate_embed(event.output)
            elif event.name == "digest_dig_deeper":
                embed = _build_dig_deeper_embed(event.output)
            else:
                continue
            if embed is not None:
                embeds.append(embed)

        return embeds[-3:]


def _looks_like_textual_tool_stub(content: str) -> bool:
    text = (content or "").lower()
    return "tool_call_name" in text and "tool_call_arguments" in text


def _is_authorized_user(*, author_id: str, authorized_id: str) -> bool:
    return author_id.strip() == authorized_id.strip()


def _build_task_list_embed(output: dict[str, Any]) -> discord.Embed:
    ok = bool(output.get("ok"))
    if not ok:
        return _build_error_embed("Task List Unavailable", output)

    tasks = output.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
    count = int(output.get("count", len(tasks)))
    embed = discord.Embed(
        title="Task Board",
        description=f"{count} tracked task(s).",
        color=discord.Color.blurple(),
    )
    embed.timestamp = datetime.now(UTC)

    if not tasks:
        embed.add_field(name="Items", value="No tasks found.", inline=False)
        return embed

    rows: list[str] = []
    for task in tasks[:15]:
        task_id = str(task.get("id", "")).strip() or "unknown"
        title = str(task.get("title", "")).strip() or "(untitled)"
        priority = str(task.get("priority", "P2")).upper()
        status = str(task.get("status", "open")).lower()
        due = str(task.get("due_at", "")).strip() or "-"
        rows.append(f"`{task_id}` | `{priority}` | `{status}` | due `{due}`")
        rows.append(title[:120])

    value = "\n".join(rows)
    embed.add_field(name="Items", value=value[:1024] or "-", inline=False)
    if count > 15:
        embed.set_footer(text=f"Showing 15 of {count} tasks.")
    return embed


def _build_task_add_embed(output: dict[str, Any]) -> discord.Embed:
    ok = bool(output.get("ok"))
    if not ok:
        return _build_error_embed("Task Creation Failed", output)

    task = output.get("task")
    if not isinstance(task, dict):
        return _build_error_embed("Task Creation Failed", {"error": "Tool output missing task payload."})

    task_id = str(task.get("id", "")).strip() or "unknown"
    title = str(task.get("title", "")).strip() or "(untitled)"
    priority = str(task.get("priority", "P2")).upper()
    due = str(task.get("due_at", "")).strip() or "-"
    description = str(task.get("description", "")).strip() or "-"
    tags = task.get("tags")
    if isinstance(tags, list) and tags:
        tag_text = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
    else:
        tag_text = "-"

    embed = discord.Embed(
        title="Task Created",
        description=title[:256],
        color=discord.Color.green(),
    )
    embed.add_field(name="Task ID", value=f"`{task_id}`", inline=True)
    embed.add_field(name="Priority", value=f"`{priority}`", inline=True)
    embed.add_field(name="Due", value=due[:128], inline=True)
    embed.add_field(name="Description", value=description[:1024], inline=False)
    embed.add_field(name="Tags", value=tag_text[:1024] or "-", inline=False)
    return embed


def _build_task_action_embed(
    output: dict[str, Any],
    *,
    success_title: str,
    success_color: discord.Color,
    failure_title: str,
    failure_color: discord.Color,
) -> discord.Embed:
    ok = bool(output.get("ok"))
    task_id = str(output.get("task_id", "")).strip() or "unknown"
    if ok:
        embed = discord.Embed(
            title=success_title,
            description=f"Task `{task_id}` updated successfully.",
            color=success_color,
        )
        return embed

    embed = discord.Embed(
        title=failure_title,
        description=f"Task `{task_id}` could not be updated.",
        color=failure_color,
    )
    error = str(output.get("error", "")).strip()
    if error:
        embed.add_field(name="Error", value=error[:1024], inline=False)
    return embed


def _build_error_embed(title: str, output: dict[str, Any]) -> discord.Embed:
    error = str(output.get("error", "Unknown error.")).strip()
    embed = discord.Embed(title=title, description=error[:2048], color=discord.Color.red())
    return embed


def _build_digest_preferences_embed(output: dict[str, Any]) -> discord.Embed:
    if not bool(output.get("ok")):
        return _build_error_embed("Digest Preferences Error", output)
    prefs = output.get("preferences")
    if not isinstance(prefs, dict):
        prefs = {}
    timezone = str(prefs.get("timezone", "UTC")).strip()
    digest_time = str(prefs.get("digest_time_local", "08:30")).strip()
    categories = prefs.get("categories")
    category_text = ", ".join(str(c) for c in categories) if isinstance(categories, list) and categories else "-"
    embed = discord.Embed(
        title="Daily Digest Preferences",
        description="Your news briefing schedule and scope.",
        color=discord.Color.dark_teal(),
    )
    embed.add_field(name="Local Time", value=f"`{digest_time}`", inline=True)
    embed.add_field(name="Timezone", value=f"`{timezone}`", inline=True)
    embed.add_field(name="Categories", value=category_text[:1024], inline=False)
    return embed


def _build_digest_embed(output: dict[str, Any]) -> discord.Embed:
    if not bool(output.get("ok")):
        return _build_error_embed("Digest Generation Failed", output)
    digest = output.get("digest")
    if not isinstance(digest, dict):
        return _build_error_embed("Digest Generation Failed", {"error": "Digest payload missing."})

    digest_id = str(digest.get("digest_id", "")).strip() or "unknown"
    summary = str(digest.get("summary", "")).strip() or "No summary available."
    categories = digest.get("categories")
    cat_text = ", ".join(str(c) for c in categories) if isinstance(categories, list) and categories else "-"
    embed = discord.Embed(
        title="Marco Morning Brief",
        description=summary[:2048],
        color=discord.Color.from_rgb(24, 118, 242),
    )
    embed.add_field(name="Digest ID", value=f"`{digest_id}`", inline=True)
    embed.add_field(name="Categories", value=cat_text[:1024], inline=True)
    created_at = str(digest.get("created_at", "")).strip()
    if created_at:
        dt = _safe_parse_iso(created_at)
        if dt is not None:
            embed.timestamp = dt

    items = digest.get("items")
    if isinstance(items, list) and items:
        rows: list[str] = []
        for idx, item in enumerate(items[:5], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            source = str(item.get("source", "")).strip() or "Unknown"
            url = str(item.get("url", "")).strip()
            if not title:
                continue
            if url:
                rows.append(f"`{idx}` [{title}]({url})")
            else:
                rows.append(f"`{idx}` {title}")
            rows.append(f"source: `{source}`")
        if rows:
            embed.add_field(name="Top Stories", value="\n".join(rows)[:1024], inline=False)
    embed.set_footer(text="Use: dig deeper <digest_id> <topic>")
    return embed


def _build_digest_recent_embed(output: dict[str, Any]) -> discord.Embed:
    if not bool(output.get("ok")):
        return _build_error_embed("Recent Digests Unavailable", output)
    rows = output.get("digests")
    if not isinstance(rows, list):
        rows = []
    embed = discord.Embed(
        title="Recent Digests",
        description="Latest generated digest IDs.",
        color=discord.Color.light_grey(),
    )
    if not rows:
        embed.add_field(name="Digests", value="No digests yet.", inline=False)
        return embed
    lines: list[str] = []
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        digest_id = str(row.get("digest_id", "")).strip() or "unknown"
        created_at = str(row.get("created_at", "")).strip() or "-"
        categories = row.get("categories")
        cat_text = ", ".join(str(c) for c in categories) if isinstance(categories, list) else "-"
        lines.append(f"`{digest_id}` | {created_at} | {cat_text}")
    embed.add_field(name="Digests", value="\n".join(lines)[:1024] or "-", inline=False)
    return embed


def _build_digest_open_rate_embed(output: dict[str, Any]) -> discord.Embed:
    if not bool(output.get("ok")):
        return _build_error_embed("Open Tracking Failed", output)
    digest_id = str(output.get("digest_id", "")).strip() or "unknown"
    rate = output.get("open_rate")
    deliveries = 0
    opens = 0
    if isinstance(rate, dict):
        deliveries = int(rate.get("deliveries", 0))
        opens = int(rate.get("opens", 0))
    ratio = (opens / deliveries * 100.0) if deliveries else 0.0
    embed = discord.Embed(
        title="Digest Engagement",
        description=f"Digest `{digest_id}` engagement snapshot.",
        color=discord.Color.brand_green(),
    )
    embed.add_field(name="Deliveries", value=str(deliveries), inline=True)
    embed.add_field(name="Opens", value=str(opens), inline=True)
    embed.add_field(name="Open Rate", value=f"{ratio:.1f}%", inline=True)
    return embed


def _build_dig_deeper_embed(output: dict[str, Any]) -> discord.Embed:
    if not bool(output.get("ok")):
        return _build_error_embed("Dig Deeper Failed", output)
    topic = str(output.get("topic", "")).strip() or "topic"
    brief = str(output.get("brief", "")).strip() or "No brief generated."
    digest_id = str(output.get("digest_id", "")).strip() or "unknown"
    embed = discord.Embed(
        title=f"Dig Deeper: {topic[:120]}",
        description=brief[:2048],
        color=discord.Color.orange(),
    )
    embed.add_field(name="Digest ID", value=f"`{digest_id}`", inline=True)
    sources = output.get("sources")
    if isinstance(sources, list) and sources:
        lines: list[str] = []
        for idx, source in enumerate(sources[:5], start=1):
            if not isinstance(source, dict):
                continue
            title = str(source.get("title", "")).strip() or "(untitled)"
            url = str(source.get("url", "")).strip()
            if url:
                lines.append(f"[{idx}] [{title}]({url})")
            else:
                lines.append(f"[{idx}] {title}")
        if lines:
            embed.add_field(name="Sources", value="\n".join(lines)[:1024], inline=False)
    return embed


def _safe_parse_iso(value: str) -> datetime | None:
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
