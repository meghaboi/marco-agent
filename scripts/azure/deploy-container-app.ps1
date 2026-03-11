param(
  [string]$ResourceGroupName = "rg-marco-agent-dev",
  [string]$AcrName,
  [string]$ContainerAppName = "marco-agent-bot",
  [string]$ImageRepository = "marco-agent",
  [string]$ImageTag = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($AcrName)) {
  throw "AcrName is required."
}

if ([string]::IsNullOrWhiteSpace($ImageTag)) {
  $ImageTag = (Get-Date -Format "yyyyMMddHHmmss")
}

Write-Host "Validating Azure login..."
$null = az account show | Out-Null

Write-Host "Ensuring containerapp extension..."
az extension add --name containerapp --upgrade --yes --output none

Write-Host "Building image in ACR (cloud build)..."
az acr build `
  --registry $AcrName `
  --image "${ImageRepository}:${ImageTag}" `
  --file "Dockerfile" `
  "." `
  --no-logs `
  --output none

$loginServer = az acr show --name $AcrName --resource-group $ResourceGroupName --query "loginServer" -o tsv
$imageRef = "${loginServer}/${ImageRepository}:${ImageTag}"

Write-Host "Updating Container App image: $imageRef"
az containerapp update `
  --name $ContainerAppName `
  --resource-group $ResourceGroupName `
  --image $imageRef `
  --output none

Write-Host ""
Write-Host "Deploy complete."
Write-Host "Container App: $ContainerAppName"
Write-Host "Image: $imageRef"
Write-Host "Tail logs with:"
Write-Host "az containerapp logs show --name $ContainerAppName --resource-group $ResourceGroupName --follow"
