from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from openai import AsyncAzureOpenAI, AsyncOpenAI

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolCallRequest:
    id: str
    name: str
    arguments_json: str


@dataclass(slots=True)
class ChatCompletionResult:
    content: str
    tool_calls: list[ToolCallRequest]
    assistant_message: dict[str, Any]


class FoundryChatClient:
    def __init__(self, endpoint: str, key: str, api_version: str) -> None:
        self._raw_endpoint = endpoint.rstrip("/")
        self._api_version = api_version
        self._client_mode = "azure_deployments"
        self._azure_client: AsyncAzureOpenAI | None = None
        self._openai_client: AsyncOpenAI | None = None

        # Azure OpenAI compatible v1 endpoints use OpenAI client with base_url.
        if self._raw_endpoint.endswith("/openai/v1"):
            self._client_mode = "openai_v1_compatible"
            self._openai_client = AsyncOpenAI(
                base_url=self._raw_endpoint,
                api_key=key,
            )
            LOGGER.info("Foundry client initialized in %s mode.", self._client_mode)
            return

        # Normalize accidental '/openai' suffix for deployment-style Azure client.
        normalized_endpoint = self._raw_endpoint
        if normalized_endpoint.endswith("/openai"):
            normalized_endpoint = normalized_endpoint[: -len("/openai")]

        self._azure_client = AsyncAzureOpenAI(
            azure_endpoint=normalized_endpoint,
            api_key=key,
            api_version=api_version,
        )
        LOGGER.info("Foundry client initialized in %s mode.", self._client_mode)

    async def chat(
        self,
        *,
        deployment: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
    ) -> str:
        result = await self.complete_messages(
            deployment=deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
        )
        return result.content

    async def complete_messages(
        self,
        *,
        deployment: str,
        messages: list[dict[str, Any]],
        temperature: float,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        try:
            payload: dict[str, Any] = {
                "model": deployment,
                "messages": messages,
                "temperature": temperature,
            }
            if tools:
                payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

            if self._client_mode == "openai_v1_compatible":
                if self._openai_client is None:
                    raise RuntimeError("OpenAI-compatible client not initialized.")
                response = await self._openai_client.chat.completions.create(**payload)
            else:
                if self._azure_client is None:
                    raise RuntimeError("Azure deployment client not initialized.")
                response = await self._azure_client.chat.completions.create(**payload)
        except Exception:
            LOGGER.exception("Azure AI Foundry chat completion failed.")
            raise

        if not response.choices:
            raise RuntimeError("No choices returned from Azure AI Foundry.")

        message = response.choices[0].message
        tool_calls, tool_call_payloads = _extract_tool_calls(message)
        content = _extract_response_content(message, has_tool_calls=bool(tool_calls))

        assistant_message: dict[str, Any] = {"role": "assistant"}
        if content:
            assistant_message["content"] = content
        if tool_call_payloads:
            assistant_message["tool_calls"] = tool_call_payloads

        return ChatCompletionResult(
            content=content,
            tool_calls=tool_calls,
            assistant_message=assistant_message,
        )

    async def embed_texts(self, *, deployment: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {
            "model": deployment,
            "input": texts,
        }
        try:
            if self._client_mode == "openai_v1_compatible":
                if self._openai_client is None:
                    raise RuntimeError("OpenAI-compatible client not initialized.")
                response = await self._openai_client.embeddings.create(**payload)
            else:
                if self._azure_client is None:
                    raise RuntimeError("Azure deployment client not initialized.")
                response = await self._azure_client.embeddings.create(**payload)
        except Exception:
            LOGGER.exception("Azure AI Foundry embedding call failed.")
            return []

        vectors: list[list[float]] = []
        for item in getattr(response, "data", []) or []:
            embedding = _read_value(item, "embedding")
            if isinstance(embedding, list):
                try:
                    vectors.append([float(v) for v in embedding])
                except (TypeError, ValueError):
                    continue
        return vectors


def _extract_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = _extract_content_item_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    if content is None:
        return ""
    return str(content).strip()


def _extract_response_content(message: Any, *, has_tool_calls: bool) -> str:
    content = _extract_content(_read_value(message, "content"))
    if content:
        return content
    # Some providers (including Kimi on Azure OpenAI-compatible endpoints)
    # may return the final user-facing post-tool text in `reasoning_content`.
    if has_tool_calls:
        return ""
    return _extract_content(_read_value(message, "reasoning_content"))


def _extract_content_item_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("text", "content", "value"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    for key in ("text", "content", "value"):
        value = getattr(item, key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_tool_calls(message: Any) -> tuple[list[ToolCallRequest], list[dict[str, Any]]]:
    parsed: list[ToolCallRequest] = []
    payloads: list[dict[str, Any]] = []

    raw_calls = _read_value(message, "tool_calls")
    if isinstance(raw_calls, list):
        for idx, raw_call in enumerate(raw_calls):
            tool_request = _parse_tool_call(raw_call, idx=idx)
            if tool_request is None:
                continue
            parsed.append(tool_request)
            payloads.append(
                {
                    "id": tool_request.id,
                    "type": "function",
                    "function": {
                        "name": tool_request.name,
                        "arguments": tool_request.arguments_json,
                    },
                }
            )

    # Legacy field returned by some providers/adapters when a single function is requested.
    if not parsed:
        legacy = _read_value(message, "function_call")
        legacy_call = _parse_legacy_function_call(legacy)
        if legacy_call is not None:
            parsed.append(legacy_call)
            payloads.append(
                {
                    "id": legacy_call.id,
                    "type": "function",
                    "function": {
                        "name": legacy_call.name,
                        "arguments": legacy_call.arguments_json,
                    },
                }
            )

    return parsed, payloads


def _parse_tool_call(raw_call: Any, *, idx: int) -> ToolCallRequest | None:
    fn = _read_value(raw_call, "function")
    if fn is None:
        return None

    name = _read_value(fn, "name") or ""
    if not name:
        return None

    args = _normalize_tool_args(_read_value(fn, "arguments"))
    call_id = _read_value(raw_call, "id") or f"tool_call_{idx}"

    return ToolCallRequest(
        id=str(call_id),
        name=str(name),
        arguments_json=args,
    )


def _parse_legacy_function_call(raw_call: Any) -> ToolCallRequest | None:
    if raw_call is None:
        return None
    name = _read_value(raw_call, "name") or ""
    if not name:
        return None
    args = _normalize_tool_args(_read_value(raw_call, "arguments"))
    return ToolCallRequest(
        id="legacy_function_call_0",
        name=str(name),
        arguments_json=args,
    )


def _normalize_tool_args(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        return text if text else "{}"
    if value is None:
        return "{}"
    try:
        return json.dumps(value, ensure_ascii=True)
    except TypeError:
        return str(value).strip() or "{}"


def _read_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
