import asyncio
from types import SimpleNamespace

from marco_agent.discord_bot import MarcoDiscordBot


class DummyTyping:
    def __init__(self) -> None:
        self.entered = False

    async def __aenter__(self):
        self.entered = True

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DummyChannel:
    def __init__(self) -> None:
        self.typing_ctx = DummyTyping()

    def typing(self):
        return self.typing_ctx

    async def send(self, _message):
        return None


class DummyAuthor:
    def __init__(self) -> None:
        self.id = 42
        self.bot = False


class DummyMessage:
    def __init__(self) -> None:
        self.author = DummyAuthor()
        self.guild = None
        self.channel = DummyChannel()
        self.content = "what's the news"


def test_on_message_scoped_uses_discord_typing_indicator() -> None:
    bot = object.__new__(MarcoDiscordBot)
    bot.file_config = SimpleNamespace(
        security=SimpleNamespace(authorized_discord_user_id="42"),
    )
    bot.memory_store = SimpleNamespace()

    async def fake_respond(_message):
        return None

    bot._respond_as_marco = fake_respond  # type: ignore[method-assign]

    message = DummyMessage()
    asyncio.run(bot._on_message_scoped(message))

    assert message.channel.typing_ctx.entered is True
