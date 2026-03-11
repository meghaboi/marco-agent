from __future__ import annotations

import logging

from openai import AsyncAzureOpenAI

LOGGER = logging.getLogger(__name__)


class FoundryChatClient:
    def __init__(self, endpoint: str, key: str, api_version: str) -> None:
        self._client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=key,
            api_version=api_version,
        )

    async def chat(
        self,
        *,
        deployment: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
    ) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=deployment,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            )
        except Exception:
            LOGGER.exception("Azure AI Foundry chat completion failed.")
            raise

        if not response.choices:
            raise RuntimeError("No choices returned from Azure AI Foundry.")

        content = response.choices[0].message.content
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                text = getattr(item, "text", None)
                if text:
                    parts.append(text)
            return "\n".join(parts).strip()
        return str(content).strip()
