# Marco - AI Chief of Staff (Azure + Discord)

Marco is a production-focused, Azure-native AI Chief of Staff that runs as a Discord bot and only serves one principal.

This repo now includes a working foundation with strict access control, Azure AI Foundry chat integration, persistent memory/task backends, configurable model routing, and a deploy-ready health probe.

## What Marco Does Today

- Enforces principal-only DM access by Discord user ID.
- Replies to any unauthorized user with exactly:
  - `I only serve meghaboi.`
- Routes chat through Azure AI Foundry deployments you select in config.
- Supports runtime model switching from Discord DM (if enabled).
- Stores conversation memory in Cosmos DB (optional).
- Stores and manages tasks in Cosmos DB (optional).
- Exposes `/healthz` endpoint for Azure Container Apps liveness/readiness probes.

## Foundation Features Implemented

- Config file-driven model registry + active model map.
- Persona seed prompt injection with short-term memory context.
- Unauthorized access logging hook.
- Task CRUD commands (`add/show/complete/delete`).
- Typed config validation (YAML + environment vars via `pydantic`).
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
  - `allow_runtime_model_switch`: enables `model use ...` commands.
  - `max_memory_messages`: number of recent messages loaded into context.
- `model_profiles`
  - Logical profile IDs mapped to Azure deployment names.
- `active_models`
  - Per-capability profile selection (`chat`, `reasoning`, `embeddings`).
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
- `PORT` (default `8080`)

## DM Commands (Authorized User Only)

### Model control

- `model list`
- `model use <chat|reasoning|embeddings> <profile_id>`

### Task management

- `add task <title> [--priority P0|P1|P2|P3] [--due YYYY-MM-DD] [--tags a,b]`
- `show tasks`
- `complete task <task_id>`
- `delete task <task_id>`

### Natural conversation

Any other DM is handled by Azure AI Foundry with persona + recent memory context.

## Security Model

- Principal access is enforced by Discord numeric user ID, not username.
- Unauthorized users always get a fixed response with no extra metadata.
- Secrets are expected via environment variables (Key Vault wiring is planned next).
- No secrets are committed to source control.

## Health and Operations

- Endpoint: `GET /healthz`
- Intended for Azure Container Apps probes.
- Logging is structured enough for immediate Azure Monitor/App Insights forwarding.

## Tests and Quality Checks

Run:

```bash
python -m pytest -q
python -m ruff check src tests
python -m compileall src
```

## Build Roadmap

The full phased implementation plan (Discord slash commands, RAG, news digest, GitHub automation, voice, video, blog, IaC, ops) is tracked in:

- `docs/microtasks.md`

## Next Milestones

- Key Vault-backed secret loading.
- Slash commands and richer Discord embeds.
- RAG pipeline (Blob + embeddings + AI Search).
- IaC (Bicep/Terraform) + GitHub Actions CI/CD to Azure Container Apps.

## License

Private project. Add license terms before public distribution.
