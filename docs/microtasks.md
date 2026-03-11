# Marco Build Micro-Tasks

This backlog is ordered for shipping a reliable v1 on Azure.

## Phase 0 - Foundation (in progress)

1. Done - Repository scaffold and dependency setup.
2. Done - Config system with model profile list and active model routing.
3. Done - Discord DM authorization gate with strict deny message.
4. Done - Azure AI Foundry chat client wrapper.
5. Done - Health probe endpoint for Container Apps.
6. In progress - Cosmos DB memory persistence schema and retrieval.

## Phase 1 - Core Assistant Loop

1. Implement slash commands: `/task`, `/brief`, `/model`, `/memory`.
2. Done - Tool-calling orchestration loop (`assistant -> tool -> assistant`) for grounded actions.
3. Done - Task tool registry + execution handlers (model selects tools).
4. In progress - Add conversation memory retrieval pipeline (recent + semantic).
5. In progress - Add structured logs + correlation IDs + App Insights hooks.
6. In progress - Add unit/integration tests for DM auth, config validation, model routing and tool routing.

## Phase 2 - Task Management (Cosmos-native)

1. Done - Design and apply `tasks` container schema.
2. Done - Implement task CRUD service with due dates, priority, tags.
3. Done - Route task intents through real model tool calls (no fabricated task data).
4. In progress - Add overdue task detector and morning summary formatter.
5. Pending - Build Discord embed rendering for tasks.
6. In progress - Add regression tests for task operations.

## Phase 3 - News Digest

1. Add preferences model for digest time, timezone, and categories.
2. Build Azure Function timer trigger for daily digest.
3. Integrate grounding and source attribution flow.
4. Implement "dig deeper" re-brief workflow.
5. Track digest delivery and open rates in Cosmos.

## Phase 4 - RAG File Understanding

1. Build Discord attachment ingestion and Blob Storage upload.
2. Add chunking + embedding indexing pipeline to Azure AI Search.
3. Build retrieval service with source citations.
4. Add file-to-project mapping and metadata tags.
5. Add compare/summarize commands and tests.

## Phase 5 - GitHub + Execution + ngrok

1. Add Key Vault-backed GitHub auth provider.
2. Add Codex account-auth flow for code execution sessions (interactive one-time login + token persistence in Key Vault).
3. Implement clone/change/branch/commit/push workflow.
4. Build PR generator with checklist templates.
5. Add execution job runner on Container Apps Job / ACI.
6. Add ngrok tunnel manager with 2-hour TTL policy.

## Phase 6 - Voice, Video, X, Blog

1. Voice call pipeline via Discord voice + Azure Speech.
2. Video generation and YouTube publish/analytics loop.
3. Twitter/X draft/approve/post/schedule flow.
4. Blog scaffold/deploy/update flow with Azure Static Web Apps.
5. Weekly growth and self-improvement reports.

## Phase 7 - IaC, Security, Ops

1. In progress - Bicep baseline for always-on Container Apps runtime + ACR + monitoring.
2. In progress - GitHub Actions CI/CD to Container Apps on `main` push.
3. Pending - Key Vault integration for all secrets.
4. Pending - Monitor dashboards, alerts, and cost budget watcher.
5. Pending - DR/backup policy and runbooks.
