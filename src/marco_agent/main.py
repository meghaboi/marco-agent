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
from marco_agent.observability import correlation_scope
from marco_agent.services.memory_retrieval import MemoryRetrievalService
from marco_agent.services.news_digest import NewsDigestService
from marco_agent.services.ai_search import AiSearchService
from marco_agent.services.attachment_ingestion import AttachmentIngestionService
from marco_agent.services.blob_storage import BlobStorageService
from marco_agent.services.codex_execution import CodexAuthSessionManager, ExecutionJobRunner
from marco_agent.services.github_ops import GitHubAuthProvider, GitHubWorkflowService
from marco_agent.services.ngrok_manager import NgrokTunnelManager
from marco_agent.services.rag_indexing import RagIndexingService
from marco_agent.services.rag_retrieval import RagRetrievalService
from marco_agent.services.secrets_provider import KeyVaultSecretProvider
from marco_agent.storage.cosmos_files import CosmosFileStore
from marco_agent.storage.cosmos_digest import CosmosDigestStore
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
    env_config = load_env_config()
    configure_logging(appinsights_connection_string=env_config.appinsights_connection_string)

    config_path = Path(os.environ.get("MARCO_CONFIG_PATH", str(DEFAULT_CONFIG_PATH)))
    file_config = load_file_config(config_path)

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
    digest_store = CosmosDigestStore(
        endpoint=env_config.cosmos_db_endpoint,
        key=env_config.cosmos_db_key,
        database_name=env_config.cosmos_db_database,
        container_name=env_config.cosmos_digest_container,
    )
    file_store = CosmosFileStore(
        endpoint=env_config.cosmos_db_endpoint,
        key=env_config.cosmos_db_key,
        database_name=env_config.cosmos_db_database,
        container_name=env_config.cosmos_files_container,
    )
    memory_retrieval = MemoryRetrievalService(
        memory_store=memory,
        ai_client=ai_client,
        file_config=file_config,
    )
    news_digest_service = NewsDigestService(
        ai_client=ai_client,
        digest_store=digest_store,
        rss_url_template=env_config.news_rss_url,
    )
    search_service = AiSearchService(
        endpoint=env_config.azure_search_endpoint,
        api_key=env_config.azure_search_key,
        index_name=env_config.azure_search_index,
        api_version=env_config.azure_search_api_version,
    )
    if search_service.enabled:
        ensured = await search_service.ensure_index()
        if not ensured:
            LOGGER.warning("Azure AI Search index bootstrap failed; RAG indexing/search may be degraded.")
    blob_service = BlobStorageService(
        connection_string=env_config.azure_blob_connection_string,
        container_name=env_config.azure_blob_container,
    )
    embedding_deployment = file_config.get_deployment_for_capability("embeddings")
    rag_indexing = RagIndexingService(
        ai_client=ai_client,
        file_store=file_store,
        ai_search=search_service,
        embedding_deployment=embedding_deployment,
        chunk_size_chars=file_config.rag.chunk_size_chars,
        chunk_overlap_chars=file_config.rag.chunk_overlap_chars,
        max_chunks_per_file=file_config.rag.max_chunks_per_file,
    )
    rag_retrieval = RagRetrievalService(
        ai_client=ai_client,
        file_store=file_store,
        ai_search=search_service,
        embedding_deployment=embedding_deployment,
    )
    attachment_ingestion = AttachmentIngestionService(
        blob_storage=blob_service,
        file_store=file_store,
        rag_indexing=rag_indexing,
        default_project=file_config.rag.default_project,
        max_file_size_mb=file_config.rag.max_file_size_mb,
    )
    secrets = KeyVaultSecretProvider(vault_url=env_config.azure_key_vault_url)
    github_auth = GitHubAuthProvider(secret_provider=secrets)
    if env_config.github_token:
        github_auth.set_user_token(
            user_id=file_config.security.authorized_discord_user_id,
            token=env_config.github_token,
        )
    if env_config.codex_account_token:
        secrets.set_secret(
            key=f"MARCO-CODEX-TOKEN-{file_config.security.authorized_discord_user_id}",
            value=env_config.codex_account_token,
        )
    github_workflow = GitHubWorkflowService(
        auth_provider=github_auth,
        clone_base_dir=file_config.github.default_clone_base_dir,
    )
    codex_auth = CodexAuthSessionManager(
        secret_provider=secrets,
        default_ttl_minutes=file_config.execution.codex.session_ttl_minutes,
    )
    execution_runner = ExecutionJobRunner(
        aca_job_name=env_config.aca_job_name,
        aca_resource_group=env_config.aca_job_resource_group,
        aci_resource_group=env_config.aci_resource_group,
        execute_commands=not env_config.execution_runner_dry_run,
    )
    ngrok = NgrokTunnelManager(
        binary=env_config.ngrok_binary,
        auth_token=env_config.ngrok_auth_token,
        max_ttl_minutes=file_config.ngrok.max_ttl_minutes,
        api_url=file_config.ngrok.api_url,
    )
    bot = MarcoDiscordBot(
        file_config=file_config,
        ai_client=ai_client,
        memory_store=memory,
        task_store=task_store,
        digest_store=digest_store,
        file_store=file_store,
        memory_retrieval=memory_retrieval,
        news_digest_service=news_digest_service,
        attachment_ingestion=attachment_ingestion,
        rag_retrieval=rag_retrieval,
        github_auth=github_auth,
        github_workflow=github_workflow,
        codex_auth=codex_auth,
        execution_runner=execution_runner,
        ngrok=ngrok,
    )

    health_runner = await start_health_server(env_config.port)
    if not env_config.discord_bot_token:
        raise ValueError("DISCORD_BOT_TOKEN is required for discord bot runtime.")

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

    with correlation_scope(prefix="startup"):
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
