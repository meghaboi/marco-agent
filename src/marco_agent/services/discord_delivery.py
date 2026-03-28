from __future__ import annotations

import logging
from typing import Any

import aiohttp

LOGGER = logging.getLogger(__name__)

DISCORD_API_BASE_URL = "https://discord.com/api/v10"


class DiscordDeliveryService:
    def __init__(self, *, bot_token: str | None) -> None:
        self._bot_token = (bot_token or "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self._bot_token)

    async def send_dm(self, *, user_id: str, content: str) -> None:
        if not self.enabled:
            raise RuntimeError("Discord delivery unavailable: missing bot token.")

        headers = {
            "Authorization": f"Bot {self._bot_token}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            channel_id = await self._create_dm_channel(session=session, user_id=user_id)
            payload = {"content": content}
            async with session.post(f"{DISCORD_API_BASE_URL}/channels/{channel_id}/messages", json=payload) as response:
                body = await response.text()
                if response.status >= 400:
                    raise RuntimeError(f"Discord message send failed ({response.status}): {body[:300]}")

    async def _create_dm_channel(self, *, session: aiohttp.ClientSession, user_id: str) -> str:
        async with session.post(
            f"{DISCORD_API_BASE_URL}/users/@me/channels",
            json={"recipient_id": user_id},
        ) as response:
            body = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"Discord DM channel creation failed ({response.status}): {body[:300]}")
            data: Any = await response.json()
        channel_id = str(data.get("id", "")).strip()
        if not channel_id:
            raise RuntimeError("Discord DM channel creation returned no channel id.")
        return channel_id
