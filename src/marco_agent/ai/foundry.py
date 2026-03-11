from __future__ import annotations

from dataclasses import dataclass
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
        content = _extract_content(message.content)
        tool_calls: list[ToolCallRequest] = []
        tool_call_payloads: list[dict[str, Any]] = []
        for call in getattr(message, "tool_calls", []) or []:
            fn = getattr(call, "function", None)
            if getattr(call, "type", None) != "function" or fn is None:
                continue
            name = getattr(fn, "name", "") or ""
            args = getattr(fn, "arguments", "") or "{}"
            call_id = getattr(call, "id", "") or ""
            tool_calls.append(
                ToolCallRequest(
                    id=call_id,
                    name=name,
                    arguments_json=args,
                )
            )
            tool_call_payloads.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                }
            )

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


def _extract_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    if content is None:
        return ""
    return str(content).strip()
