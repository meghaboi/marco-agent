# Azure 24/7 Deployment + CI/CD

This guide deploys Marco to Azure Container Apps with `minReplicas=1` for always-on runtime, then configures GitHub Actions for auto-deploy on push to `main`.

## 1) Provision Infra (One-time)

Prerequisites:
- Azure CLI logged in (`az login`)
- `.env` contains:
  - `DISCORD_BOT_TOKEN`
  - `AZURE_AI_FOUNDRY_ENDPOINT`
  - `AZURE_AI_FOUNDRY_KEY`
  - Cosmos values (optional but recommended)

Run:

```powershell
pwsh ./scripts/azure/provision-container-app.ps1 `
  -ResourceGroupName rg-marco-agent-dev `
  -Location centralindia `
  -Prefix marcoagent `
  -ContainerAppName marco-agent-bot `
  -ManagedEnvironmentName marco-agent-cae
```

What this creates:
- Log Analytics workspace
- Application Insights
- Container Apps environment
- Azure Container Registry (Basic)
- Bootstrap image built in ACR
- Azure Container App with:
  - `minReplicas: 1`
  - health probes on `/healthz`
  - runtime env vars + secrets

## 2) Deploy Current Code

```powershell
pwsh ./scripts/azure/deploy-container-app.ps1 `
  -ResourceGroupName rg-marco-agent-dev `
  -AcrName <your-acr-name> `
  -ContainerAppName marco-agent-bot
```

Verify:

```powershell
az containerapp logs show --name marco-agent-bot --resource-group rg-marco-agent-dev --follow
```

## 3) Configure GitHub Actions Auto-Deploy

Add repository secrets:

- `AZURE_CREDENTIALS`
- `AZURE_RESOURCE_GROUP`
- `AZURE_ACR_NAME`
- `AZURE_CONTAINER_APP_NAME`

Workflow file:
- `.github/workflows/deploy-containerapp.yml`

On every push to `main`, the workflow:
1. runs tests/lint
2. builds image in ACR (`az acr build`)
3. updates Azure Container App image

## 4) Create `AZURE_CREDENTIALS` Secret

Create service principal (Contributor scoped to your resource group):

```bash
az ad sp create-for-rbac \
  --name "marco-gha-deployer" \
  --role Contributor \
  --scopes /subscriptions/<SUB_ID>/resourceGroups/rg-marco-agent-dev \
  --sdk-auth
```

Copy JSON output into GitHub secret `AZURE_CREDENTIALS`.

## 5) Notes

- Container App is internal by default; Marco works through Discord gateway, not public HTTP.
- Keep `config/marco.config.yaml` committed and non-secret.
- Keep all secrets in Azure/GitHub secrets only, never in git.
