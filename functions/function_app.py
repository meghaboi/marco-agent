from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import azure.functions as func
from dotenv import load_dotenv

from marco_agent.ai.foundry import FoundryChatClient
from marco_agent.config import DEFAULT_CONFIG_PATH, load_env_config, load_file_config
from marco_agent.logging_config import configure_logging
from marco_agent.observability import correlation_scope, new_correlation_id
from marco_agent.services.digest_scheduler import DigestScheduler
from marco_agent.services.discord_delivery import DiscordDeliveryService
from marco_agent.services.news_digest import NewsDigestService
from marco_agent.storage.cosmos_digest import CosmosDigestStore

LOGGER = logging.getLogger(__name__)
app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
_RUNTIME: dict[str, Any] = {}

TRACKING_PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00"
    b"\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00"
    b"\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _get_runtime() -> dict[str, Any]:
    global _RUNTIME
    if _RUNTIME:
        return _RUNTIME
    load_dotenv()
    env = load_env_config()
    configure_logging(appinsights_connection_string=env.appinsights_connection_string)
    config_path = Path(os.environ.get("MARCO_CONFIG_PATH", str(DEFAULT_CONFIG_PATH)))
    file_cfg = load_file_config(config_path)
    ai_client = FoundryChatClient(
        endpoint=env.azure_ai_foundry_endpoint,
        key=env.azure_ai_foundry_key,
        api_version=env.azure_ai_foundry_api_version,
    )
    digest_store = CosmosDigestStore(
        endpoint=env.cosmos_db_endpoint,
        key=env.cosmos_db_key,
        database_name=env.cosmos_db_database,
        container_name=env.cosmos_digest_container,
    )
    digest_service = NewsDigestService(
        ai_client=ai_client,
        digest_store=digest_store,
        rss_url_template=env.news_rss_url,
    )
    scheduler = DigestScheduler(
        digest_store=digest_store,
        digest_service=digest_service,
        file_config=file_cfg,
        discord_delivery=DiscordDeliveryService(bot_token=env.discord_bot_token),
    )
    _RUNTIME = {
        "env": env,
        "cfg": file_cfg,
        "digest_store": digest_store,
        "scheduler": scheduler,
    }
    return _RUNTIME


@app.timer_trigger(
    schedule="%DIGEST_TIMER_SCHEDULE%",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def daily_digest_timer(timer: func.TimerRequest) -> None:
    corr = new_correlation_id(prefix="digest-timer")
    with correlation_scope(value=corr):
        runtime = _get_runtime()
        cfg = runtime["cfg"]
        reasoning_deployment = cfg.get_deployment_for_capability("reasoning")
        result = asyncio.run(runtime["scheduler"].run_due(reasoning_deployment=reasoning_deployment))
        LOGGER.info(
            "Digest timer executed. attempted=%s generated=%s skipped=%s errors=%s past_due=%s",
            result.attempted,
            result.generated,
            result.skipped,
            result.errors,
            timer.past_due,
        )


@app.route(route="digest/open", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def digest_open(req: func.HttpRequest) -> func.HttpResponse:
    corr = new_correlation_id(prefix="digest-open")
    with correlation_scope(value=corr):
        runtime = _get_runtime()
        digest_store: CosmosDigestStore = runtime["digest_store"]
        user_id = (req.params.get("user_id") or "").strip()
        digest_id = (req.params.get("digest_id") or "").strip()
        source = (req.params.get("source") or "link").strip()
        if user_id and digest_id:
            digest_store.track_open(user_id=user_id, digest_id=digest_id, source=source)
        return func.HttpResponse(
            body=TRACKING_PIXEL_GIF,
            mimetype="image/gif",
            status_code=200,
        )


@app.route(route="digest/embed", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def digest_embed(req: func.HttpRequest) -> func.HttpResponse:
    corr = new_correlation_id(prefix="digest-embed")
    with correlation_scope(value=corr):
        runtime = _get_runtime()
        digest_store: CosmosDigestStore = runtime["digest_store"]
        user_id = (req.params.get("user_id") or "").strip()
        digest_id = (req.params.get("digest_id") or "").strip()
        if not user_id or not digest_id:
            return func.HttpResponse("Missing user_id or digest_id.", status_code=400)
        digest = digest_store.get_digest(user_id=user_id, digest_id=digest_id)
        if not digest:
            return func.HttpResponse("Digest not found.", status_code=404)
        html = _render_digest_embed(digest=digest)
        return func.HttpResponse(html, status_code=200, mimetype="text/html")


def _render_digest_embed(*, digest: dict[str, Any]) -> str:
    summary = str(digest.get("summary", "")).strip()
    digest_id = str(digest.get("digest_id", "")).strip()
    created_at = str(digest.get("created_at", "")).strip()
    items = digest.get("items")
    if not isinstance(items, list):
        items = []
    rows = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        source = str(item.get("source", "")).strip()
        url = str(item.get("url", "")).strip()
        if not title:
            continue
        if url:
            rows.append(f"<li><a href='{url}' target='_blank' rel='noopener'>{title}</a><span>{source}</span></li>")
        else:
            rows.append(f"<li><span>{title}</span><span>{source}</span></li>")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Marco Digest {digest_id}</title>
  <style>
    :root {{
      --bg: #f4f6fb;
      --ink: #0f172a;
      --accent: #1d4ed8;
      --card: #ffffff;
      --muted: #475569;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(circle at 0% 0%, #dbeafe, var(--bg) 45%);
      color: var(--ink);
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 20px;
    }}
    .card {{
      width: min(860px, 100%);
      background: var(--card);
      border-radius: 20px;
      border: 1px solid #dbe3f4;
      box-shadow: 0 20px 50px rgba(15, 23, 42, 0.1);
      overflow: hidden;
    }}
    .head {{
      padding: 26px 28px;
      background: linear-gradient(135deg, #eff6ff, #dbeafe);
      border-bottom: 1px solid #dbeafe;
    }}
    .head h1 {{ margin: 0 0 6px; font-size: clamp(1.1rem, 2vw, 1.5rem); }}
    .meta {{ color: var(--muted); font-size: .9rem; }}
    .summary {{
      padding: 20px 28px 6px;
      line-height: 1.5;
      white-space: pre-wrap;
    }}
    ul {{
      margin: 0;
      padding: 12px 28px 26px 45px;
      display: grid;
      gap: 10px;
    }}
    li {{
      display: grid;
      gap: 4px;
      animation: rise .45s ease both;
    }}
    li a {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 600;
    }}
    li span {{ color: var(--muted); font-size: .86rem; }}
    @keyframes rise {{
      from {{ opacity: 0; transform: translateY(5px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
  </style>
</head>
<body>
  <article class="card">
    <header class="head">
      <h1>Marco Morning Brief</h1>
      <div class="meta">digest: {digest_id} | created: {created_at}</div>
    </header>
    <section class="summary">{summary}</section>
    <ul>{"".join(rows) or "<li><span>No stories in this digest.</span></li>"}</ul>
  </article>
</body>
</html>"""
