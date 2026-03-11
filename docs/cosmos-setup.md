# Cosmos Setup (Azure)

This project includes a provisioning script for Cosmos DB (SQL API, serverless).

## What It Creates

- Resource group (if missing)
- Cosmos DB account
- SQL database (`marco` by default)
- Memory container (`conversation_memory`)
- Tasks container (`tasks`)

Both containers use partition key: `/partition_key`.

## Run

```powershell
pwsh ./scripts/azure/provision-cosmos.ps1
```

Optional custom values:

```powershell
pwsh ./scripts/azure/provision-cosmos.ps1 `
  -ResourceGroupName rg-marco-prod `
  -Location eastus `
  -AccountName marcoagent123456 `
  -DatabaseName marco `
  -MemoryContainer conversation_memory `
  -TasksContainer tasks
```

## What The Script Updates

If `-UpdateLocalEnv` is enabled (default), it writes these keys into local `.env`:

- `COSMOS_DB_ENDPOINT`
- `COSMOS_DB_KEY`
- `COSMOS_DB_DATABASE`
- `COSMOS_DB_CONTAINER`
- `COSMOS_TASKS_CONTAINER`

## After Provisioning

1. Start the bot:
   ```powershell
   python -m marco_agent.main
   ```
2. In Discord DM (authorized user):
   - `add task Ship v1 --priority P1`
   - `show tasks`

If those commands work and persist across restarts, Cosmos is wired correctly.
