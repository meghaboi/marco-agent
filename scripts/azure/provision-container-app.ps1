param(
  [string]$ResourceGroupName = "rg-marco-agent-dev",
  [string]$Location = "centralindia",
  [string]$Prefix = "marcoagent",
  [string]$ContainerAppName = "marco-agent-bot",
  [string]$ManagedEnvironmentName = "marco-agent-cae",
  [string]$AcrName = "",
  [string]$ImageRepository = "marco-agent",
  [string]$ImageTag = ""
)

$ErrorActionPreference = "Stop"

function Get-EnvFileValue {
  param(
    [string]$Path,
    [string]$Key
  )
  if (-not (Test-Path $Path)) {
    return $null
  }
  $line = Get-Content $Path | Where-Object { $_ -match "^$([regex]::Escape($Key))=" } | Select-Object -First 1
  if (-not $line) {
    return $null
  }
  return ($line -split "=", 2)[1]
}

function Assert-RequiredValue {
  param(
    [string]$Name,
    [string]$Value
  )
  if ([string]::IsNullOrWhiteSpace($Value)) {
    throw "Missing required value: $Name"
  }
}

function Coalesce-String {
  param(
    [string]$Value,
    [string]$Fallback = ""
  )
  if ($null -eq $Value) { return $Fallback }
  return $Value
}

function New-UniqueAcrName {
  param([string]$Base)
  $safeBase = ($Base.ToLower() -replace "[^a-z0-9]", "")
  if ($safeBase.Length -lt 5) {
    $safeBase = "marco"
  }
  if ($safeBase.Length -gt 20) {
    $safeBase = $safeBase.Substring(0, 20)
  }

  for ($i = 0; $i -lt 30; $i++) {
    $suffix = Get-Random -Minimum 100000 -Maximum 999999
    $candidate = "$safeBase$suffix"
    $available = az acr check-name --name $candidate --query "nameAvailable" -o tsv
    if ($available -eq "true") {
      return $candidate
    }
  }
  throw "Unable to generate an available ACR name."
}

Write-Host "Validating Azure login..."
$null = az account show | Out-Null

$envPath = ".env"
$discordBotToken = Get-EnvFileValue -Path $envPath -Key "DISCORD_BOT_TOKEN"
$foundryEndpoint = Get-EnvFileValue -Path $envPath -Key "AZURE_AI_FOUNDRY_ENDPOINT"
$foundryKey = Get-EnvFileValue -Path $envPath -Key "AZURE_AI_FOUNDRY_KEY"
$foundryApiVersion = Get-EnvFileValue -Path $envPath -Key "AZURE_AI_FOUNDRY_API_VERSION"
$cosmosEndpoint = Get-EnvFileValue -Path $envPath -Key "COSMOS_DB_ENDPOINT"
$cosmosKey = Get-EnvFileValue -Path $envPath -Key "COSMOS_DB_KEY"
$cosmosDatabase = Get-EnvFileValue -Path $envPath -Key "COSMOS_DB_DATABASE"
$cosmosContainer = Get-EnvFileValue -Path $envPath -Key "COSMOS_DB_CONTAINER"
$cosmosTasksContainer = Get-EnvFileValue -Path $envPath -Key "COSMOS_TASKS_CONTAINER"

Assert-RequiredValue -Name "DISCORD_BOT_TOKEN" -Value $discordBotToken
Assert-RequiredValue -Name "AZURE_AI_FOUNDRY_ENDPOINT" -Value $foundryEndpoint
Assert-RequiredValue -Name "AZURE_AI_FOUNDRY_KEY" -Value $foundryKey

if ([string]::IsNullOrWhiteSpace($foundryApiVersion)) { $foundryApiVersion = "2024-10-21" }
if ([string]::IsNullOrWhiteSpace($cosmosDatabase)) { $cosmosDatabase = "marco" }
if ([string]::IsNullOrWhiteSpace($cosmosContainer)) { $cosmosContainer = "conversation_memory" }
if ([string]::IsNullOrWhiteSpace($cosmosTasksContainer)) { $cosmosTasksContainer = "tasks" }

if ([string]::IsNullOrWhiteSpace($AcrName)) {
  $AcrName = New-UniqueAcrName -Base $Prefix
}
if ([string]::IsNullOrWhiteSpace($ImageTag)) {
  $ImageTag = "bootstrap-" + (Get-Date -Format "yyyyMMddHHmmss")
}

Write-Host "Ensuring providers are registered..."
az provider register --namespace Microsoft.App --output none
az provider register --namespace Microsoft.OperationalInsights --output none
az provider register --namespace Microsoft.ContainerRegistry --output none

Write-Host "Creating resource group (idempotent): $ResourceGroupName"
az group create --name $ResourceGroupName --location $Location --output none

$acrExists = az acr list `
  --resource-group $ResourceGroupName `
  --query "[?name=='$AcrName'] | length(@)" `
  -o tsv
if ($acrExists -eq "0") {
  Write-Host "Creating ACR (idempotent target): $AcrName"
  az acr create `
    --name $AcrName `
    --resource-group $ResourceGroupName `
    --location $Location `
    --sku Basic `
    --admin-enabled true `
    --output none
} else {
  Write-Host "ACR already exists: $AcrName"
}

Write-Host "Building bootstrap image in ACR: ${ImageRepository}:${ImageTag}"
az acr build `
  --registry $AcrName `
  --image "${ImageRepository}:${ImageTag}" `
  --file "Dockerfile" `
  "." `
  --no-logs `
  --output none

$paramObj = @{
  location = @{ value = $Location }
  prefix = @{ value = $Prefix }
  managedEnvironmentName = @{ value = $ManagedEnvironmentName }
  containerAppName = @{ value = $ContainerAppName }
  acrName = @{ value = $AcrName }
  imageRepository = @{ value = $ImageRepository }
  imageTag = @{ value = $ImageTag }
  discordBotToken = @{ value = $discordBotToken }
  azureAiFoundryEndpoint = @{ value = $foundryEndpoint }
  azureAiFoundryKey = @{ value = $foundryKey }
  azureAiFoundryApiVersion = @{ value = $foundryApiVersion }
  cosmosDbEndpoint = @{ value = (Coalesce-String -Value $cosmosEndpoint -Fallback "") }
  cosmosDbKey = @{ value = (Coalesce-String -Value $cosmosKey -Fallback "") }
  cosmosDbDatabase = @{ value = $cosmosDatabase }
  cosmosDbContainer = @{ value = $cosmosContainer }
  cosmosTasksContainer = @{ value = $cosmosTasksContainer }
}

$tmpParamsPath = Join-Path $env:TEMP ("marco-ca-params-" + [guid]::NewGuid().ToString() + ".json")
@{
  '$schema' = "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#"
  contentVersion = "1.0.0.0"
  parameters = $paramObj
} | ConvertTo-Json -Depth 10 | Set-Content -Path $tmpParamsPath

Write-Host "Deploying Container Apps infra via Bicep..."
$deploymentJson = az deployment group create `
  --resource-group $ResourceGroupName `
  --template-file "infra/main.bicep" `
  --parameters "@$tmpParamsPath" `
  -o json

if ($LASTEXITCODE -ne 0) {
  Remove-Item -Force $tmpParamsPath
  throw "Bicep deployment failed."
}

$deployment = $deploymentJson | ConvertFrom-Json
$state = $deployment.properties.provisioningState
if ($state -ne "Succeeded") {
  Remove-Item -Force $tmpParamsPath
  throw "Bicep deployment provisioning state: $state"
}

$outputs = $deployment.properties.outputs

Remove-Item -Force $tmpParamsPath

$acrLoginServer = $outputs.acrLoginServer.value
$containerApp = $outputs.containerAppNameOut.value

Write-Host ""
Write-Host "Provision complete."
Write-Host "Resource Group: $ResourceGroupName"
Write-Host "Container App: $containerApp"
Write-Host "ACR Name: $AcrName"
Write-Host "ACR Login Server: $acrLoginServer"
Write-Host "Initial image tag: $ImageTag"
Write-Host ""
Write-Host "Next:"
Write-Host "1) For next code changes, build and push a new image:"
Write-Host "   ./scripts/azure/deploy-container-app.ps1 -ResourceGroupName $ResourceGroupName -AcrName $AcrName -ContainerAppName $containerApp"
Write-Host "2) Add GitHub secrets for CI/CD:"
Write-Host "   AZURE_CREDENTIALS, AZURE_RESOURCE_GROUP, AZURE_CONTAINER_APP_NAME, AZURE_ACR_NAME"
