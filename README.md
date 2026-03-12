# Marco - AI Chief of Staff (Azure + Discord)

Marco is a production-focused, Azure-native AI Chief of Staff that runs as a Discord bot and only serves one principal.

This repo now includes a working foundation with strict access control, Azure AI Foundry chat integration, persistent memory/task backends, configurable model routing, and a deploy-ready health probe.

## What Marco Does Today

- Enforces principal-only DM access by Discord user ID.
- Replies to any unauthorized user with exactly:
  - `I only serve meghaboi.`
- Routes chat and tool orchestration through Azure AI Foundry (`kimi-k2.5` by default).
- Runtime model switching is disabled by default for stable routing.
- Stores conversation memory in Cosmos DB (optional).
- Adds semantic memory retrieval (recent + vector similarity) for richer context windows.
- Stores and manages tasks in Cosmos DB (optional).
- Supports grounded news digests with citations, preferences, and dig-deeper re-briefs.
- Tracks digest deliveries and opens in Cosmos DB.
- Exposes `/healthz` endpoint for Azure Container Apps liveness/readiness probes.

## Foundation Features Implemented

- Config file-driven model registry + active model map.
- Persona seed prompt injection with short-term memory context.
- Unauthorized access logging hook.
- Task CRUD commands (`add/show/complete/delete`).
- Typed config validation (YAML + environment vars via `pydantic`).
- Structured logs with correlation IDs and optional App Insights sink.
- Test coverage for config integrity + command parsing.

## Architecture (Current Slice)

```text
Discord DM
   |
   v
Marco Bot (discord.py) ----------------------+
   |                                          |
   | authorized user only                     | unauthorized attempts
   v                                          v
Azure AI Foundry Chat Client             Cosmos DB (security log)
   |
   +--> response back to Discord
   |
   +--> Cosmos DB memory (conversation history)
   |
   +--> Cosmos DB tasks (task CRUD)

Health endpoint (/healthz) via aiohttp for Container Apps probes
```

## Tool-First Orchestration (Mandatory)

Marco now follows a strict tool-calling loop for operational actions:

1. User message enters the assistant.
2. Model decides whether to call tools.
3. Marco executes tool calls against real systems (Cosmos, later GitHub/Blob/Search/etc.).
4. Tool outputs are returned to the model.
5. Model produces the grounded final response.

For task operations, Marco must use tools and must not fabricate task data.
This is the required architecture for future features as well (news, RAG, GitHub, publishing, analytics).

## Repository Layout

```text
config/
  marco.config.yaml          # Model profiles, active routing, persona, security config
docs/
  microtasks.md              # Full phased build backlog
src/marco_agent/
  ai/foundry.py              # Azure AI Foundry client wrapper
  config.py                  # YAML + env validation models
  discord_bot.py             # DM handler, auth gate, model/task commands
  main.py                    # App entrypoint and health server
  storage/
    cosmos_memory.py         # Conversation + unauthorized logs
    cosmos_tasks.py          # Task CRUD backend
tests/
  test_config.py
  test_task_parser.py
```

## Prerequisites

- Python `3.11+`
- Discord bot token and the principal's numeric Discord user ID
- Azure AI Foundry endpoint + key + deployed chat model(s)
- (Optional) Cosmos DB endpoint/key for memory and tasks

## Quick Start (Local)

1. Create a virtual environment and activate it.
2. Install dependencies:
   ```bash
   pip install -e .[dev]
   ```
3. Copy environment template:
   ```bash
   cp .env.example .env
   ```
   On Windows PowerShell:
   ```powershell
   Copy-Item .env.example .env
   ```
4. Fill `.env` with your secrets.
5. Edit `config/marco.config.yaml`:
   - Set `security.authorized_discord_user_id`.
   - Set each `model_profiles[].azure_deployment` to your actual Azure deployment names.
6. Run:
   ```bash
   python -m marco_agent.main
   ```

## Configuration Reference

### `config/marco.config.yaml`

- `security`
  - `authorized_discord_user_id`: the only user allowed to use Marco.
  - `unauthorized_message`: keep as `I only serve meghaboi.` to preserve strict policy.
- `assistant`
  - `allow_runtime_model_switch`: defaults to `false`; set `true` to enable `model use ...` commands.
  - `max_memory_messages`: number of recent messages loaded into context.
- `model_profiles`
  - Logical profile IDs mapped to Azure deployment names.
- `active_models`
  - Per-capability profile selection (`chat`, `reasoning`, `embeddings`).
  - Default config pins both `chat` and `reasoning` to `kimi-k2.5`.
  - Tool orchestration runs on `reasoning`; choose a model with native structured tool-calling support if you override.
- `execution.codex`
  - Reserved for upcoming Codex-backed execution sessions.

### `.env` keys

- `DISCORD_BOT_TOKEN`
- `AZURE_AI_FOUNDRY_ENDPOINT`
- `AZURE_AI_FOUNDRY_KEY`
- `AZURE_AI_FOUNDRY_API_VERSION` (default `2024-10-21`)
- `COSMOS_DB_ENDPOINT` (optional)
- `COSMOS_DB_KEY` (optional)
- `COSMOS_DB_DATABASE` (default `marco`)
- `COSMOS_DB_CONTAINER` (default `conversation_memory`)
- `COSMOS_TASKS_CONTAINER` (default `tasks`)
- `COSMOS_DIGEST_CONTAINER` (default `news_digest`)
- `APPLICATIONINSIGHTS_CONNECTION_STRING` (optional, for Azure Monitor/App Insights)
- `NEWS_RSS_URL_TEMPLATE` (default Google News RSS search template)
- `DIGEST_TIMER_SCHEDULE` (default `0 30 2 * * *` UTC)
- `DIGEST_OPEN_TRACKING_BASE_URL` (optional)
- `PORT` (default `8080`)

## DM Commands (Authorized User Only)

### Model control

- `model list`
- `model use <chat|reasoning|embeddings> <profile_id>` (only when `allow_runtime_model_switch: true`)

### Task management

- Natural language is supported and routed through model tool-calls.
- Example prompts:
  - `add task Ship v1 with p1 priority`
  - `show tasks`
  - `complete task abc12345`
  - `delete task abc12345`
  - `what are my open tasks right now?`

### Natural conversation

Any other DM is handled by Azure AI Foundry with persona + recent memory context.

## Security Model

- Principal access is enforced by Discord numeric user ID, not username.
- Unauthorized users always get a fixed response with no extra metadata.
- Secrets are expected via environment variables (Key Vault wiring is planned next).
- No secrets are committed to source control.

## Reliability Rule

Operational responses that require state changes or retrieval must come from tool outputs.
If a tool is unavailable, Marco must explicitly report that instead of hallucinating success.
The Foundry client also normalizes multiple tool-call/content response formats to reduce empty-response failures.

## Health and Operations

- Endpoint: `GET /healthz`
- Intended for Azure Container Apps probes.
- Logging is structured enough for immediate Azure Monitor/App Insights forwarding.

## Tests and Quality Checks

Run:

```bash
python -m pytest -q
python -m ruff check src tests functions
python -m compileall src
```

## Azure Functions (Phase 3 Digest)

`functions/function_app.py` includes:

- Timer trigger: `daily_digest_timer` (`%DIGEST_TIMER_SCHEDULE%`)
- HTTP endpoint: `GET /api/digest/open` (open tracking pixel)
- HTTP endpoint: `GET /api/digest/embed` (custom digest web card UI)

## Build Roadmap

The full phased implementation plan (Discord slash commands, RAG, news digest, GitHub automation, voice, video, blog, IaC, ops) is tracked in:

- `docs/microtasks.md`

## Azure Cosmos First-Run

Use the provisioning script:

```powershell
pwsh ./scripts/azure/provision-cosmos.ps1
```

Detailed guide:

- `docs/cosmos-setup.md`

## Azure 24/7 Deployment

Marco can run continuously on Azure Container Apps (`minReplicas=1`) with push-to-deploy via GitHub Actions.

Start here:

- `docs/azure-deploy.md`

Key assets:

- IaC: `infra/main.bicep`
- Provision script: `scripts/azure/provision-container-app.ps1`
- Deploy script: `scripts/azure/deploy-container-app.ps1`
- CI/CD workflow: `.github/workflows/deploy-containerapp.yml`

## Next Milestones

- Key Vault-backed secret loading.
- Slash commands and richer Discord embeds.
- RAG pipeline (Blob + embeddings + AI Search).
- IaC (Bicep/Terraform) + GitHub Actions CI/CD to Azure Container Apps.

## License

Private project. Add license terms before public distribution.
