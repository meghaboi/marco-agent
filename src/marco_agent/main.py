from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

from marco_agent.ai.foundry import FoundryChatClient
from marco_agent.config import DEFAULT_CONFIG_PATH, load_env_config, load_file_config
from marco_agent.discord_bot import MarcoDiscordBot
from marco_agent.logging_config import configure_logging
from marco_agent.storage.cosmos_memory import CosmosMemoryStore
from marco_agent.storage.cosmos_tasks import CosmosTaskStore

LOGGER = logging.getLogger(__name__)


async def start_health_server(port: int) -> web.AppRunner:
    app = web.Application()

    async def health_handler(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app.router.add_get("/healthz", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    LOGGER.info("Health endpoint ready on port %s at /healthz", port)
    return runner


async def run() -> None:
    load_dotenv()
    configure_logging()

    config_path = Path(os.environ.get("MARCO_CONFIG_PATH", str(DEFAULT_CONFIG_PATH)))
    file_config = load_file_config(config_path)
    env_config = load_env_config()

    memory = CosmosMemoryStore(
        endpoint=env_config.cosmos_db_endpoint,
        key=env_config.cosmos_db_key,
        database_name=env_config.cosmos_db_database,
        container_name=env_config.cosmos_db_container,
    )
    ai_client = FoundryChatClient(
        endpoint=env_config.azure_ai_foundry_endpoint,
        key=env_config.azure_ai_foundry_key,
        api_version=env_config.azure_ai_foundry_api_version,
    )
    task_store = CosmosTaskStore(
        endpoint=env_config.cosmos_db_endpoint,
        key=env_config.cosmos_db_key,
        database_name=env_config.cosmos_db_database,
        container_name=env_config.cosmos_tasks_container,
    )
    bot = MarcoDiscordBot(
        file_config=file_config,
        ai_client=ai_client,
        memory_store=memory,
        task_store=task_store,
    )

    health_runner = await start_health_server(env_config.port)

    stop_event = asyncio.Event()

    def _stop_signal_handler() -> None:
        LOGGER.info("Stop signal received.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _stop_signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _stop_signal_handler)
    except NotImplementedError:
        LOGGER.warning("Signal handlers are not supported in this runtime.")

    bot_task = asyncio.create_task(bot.start(env_config.discord_bot_token), name="discord-bot")
    wait_task = asyncio.create_task(stop_event.wait(), name="stop-wait")

    done, _ = await asyncio.wait(
        {bot_task, wait_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in done:
        if task is bot_task and task.exception():
            raise task.exception()

    await bot.close()
    await health_runner.cleanup()
    LOGGER.info("Marco shutdown complete.")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
