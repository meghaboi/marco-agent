param(
  [string]$ResourceGroupName = "rg-marco-agent-dev",
  [string]$Location = "centralindia",
  [string]$AccountName = "",
  [string]$DatabaseName = "marco",
  [string]$MemoryContainer = "conversation_memory",
  [string]$TasksContainer = "tasks",
  [switch]$UpdateLocalEnv = $true
)

$ErrorActionPreference = "Stop"

function Assert-AzLoggedIn {
  try {
    $null = az account show | Out-Null
  } catch {
    throw "Azure CLI is not logged in. Run: az login"
  }
}

function New-CosmosAccountName {
  for ($i = 0; $i -lt 20; $i++) {
    $candidate = "marcoagent" + (Get-Random -Minimum 100000 -Maximum 999999)
    $exists = az cosmosdb check-name-exists --name $candidate -o tsv
    if ($exists.ToString().Trim().ToLower() -eq "false") {
      return $candidate
    }
  }
  throw "Failed to generate a unique Cosmos account name after multiple attempts."
}

function Set-Or-AddEnvValue {
  param(
    [string]$Path,
    [string]$Key,
    [string]$Value
  )
  $escapedKey = [regex]::Escape($Key)
  if (-not (Test-Path $Path)) {
    Set-Content -Path $Path -Value "$Key=$Value"
    return
  }

  $content = @(Get-Content -Path $Path)
  $pattern = "^$escapedKey=.*$"
  $updated = $false
  for ($i = 0; $i -lt $content.Count; $i++) {
    if ($content[$i] -match $pattern) {
      $content[$i] = "$Key=$Value"
      $updated = $true
    }
  }
  if (-not $updated) {
    $content = @($content) + "$Key=$Value"
  }
  Set-Content -Path $Path -Value $content
}

Write-Host "Checking Azure login..."
Assert-AzLoggedIn

$subscriptionId = az account show --query "id" -o tsv
$subscriptionName = az account show --query "name" -o tsv
Write-Host "Using subscription: $subscriptionName ($subscriptionId)"

if ([string]::IsNullOrWhiteSpace($AccountName)) {
  $AccountName = New-CosmosAccountName
  Write-Host "Generated Cosmos account name: $AccountName"
}

Write-Host "Creating resource group (idempotent): $ResourceGroupName in $Location"
az group create `
  --name $ResourceGroupName `
  --location $Location `
  --output none

$accountExists = az cosmosdb list `
  --resource-group $ResourceGroupName `
  --query "[?name=='$AccountName'] | length(@)" `
  -o tsv

if ($accountExists -eq "0") {
  Write-Host "Creating Cosmos DB account: $AccountName"
  az cosmosdb create `
    --name $AccountName `
    --resource-group $ResourceGroupName `
    --locations regionName=$Location failoverPriority=0 isZoneRedundant=false `
    --default-consistency-level Session `
    --capabilities EnableServerless `
    --output none
} else {
  Write-Host "Cosmos DB account already exists: $AccountName"
}

Write-Host "Creating SQL database (idempotent): $DatabaseName"
az cosmosdb sql database create `
  --account-name $AccountName `
  --resource-group $ResourceGroupName `
  --name $DatabaseName `
  --output none

Write-Host "Creating memory container (idempotent): $MemoryContainer"
az cosmosdb sql container create `
  --account-name $AccountName `
  --resource-group $ResourceGroupName `
  --database-name $DatabaseName `
  --name $MemoryContainer `
  --partition-key-path "/partition_key" `
  --output none

Write-Host "Creating tasks container (idempotent): $TasksContainer"
az cosmosdb sql container create `
  --account-name $AccountName `
  --resource-group $ResourceGroupName `
  --database-name $DatabaseName `
  --name $TasksContainer `
  --partition-key-path "/partition_key" `
  --output none

$endpoint = az cosmosdb show `
  --name $AccountName `
  --resource-group $ResourceGroupName `
  --query "documentEndpoint" `
  -o tsv

$key = az cosmosdb keys list `
  --name $AccountName `
  --resource-group $ResourceGroupName `
  --type keys `
  --query "primaryMasterKey" `
  -o tsv

$connectionString = "AccountEndpoint=$endpoint;AccountKey=$key;"

if ($UpdateLocalEnv) {
  $envPath = ".env"
  Write-Host "Updating local .env with Cosmos values..."
  Set-Or-AddEnvValue -Path $envPath -Key "COSMOS_DB_ENDPOINT" -Value $endpoint
  Set-Or-AddEnvValue -Path $envPath -Key "COSMOS_DB_KEY" -Value $key
  Set-Or-AddEnvValue -Path $envPath -Key "COSMOS_DB_DATABASE" -Value $DatabaseName
  Set-Or-AddEnvValue -Path $envPath -Key "COSMOS_DB_CONTAINER" -Value $MemoryContainer
  Set-Or-AddEnvValue -Path $envPath -Key "COSMOS_TASKS_CONTAINER" -Value $TasksContainer
}

Write-Host ""
Write-Host "Cosmos setup complete."
Write-Host "Resource Group: $ResourceGroupName"
Write-Host "Account: $AccountName"
Write-Host "Endpoint: $endpoint"
Write-Host "Database: $DatabaseName"
Write-Host "Memory Container: $MemoryContainer"
Write-Host "Tasks Container: $TasksContainer"
Write-Host ""
Write-Host "Connection string assembled in-memory and values written to .env (if enabled)."
Write-Host "Store COSMOS_DB_KEY in Azure Key Vault before production use."
