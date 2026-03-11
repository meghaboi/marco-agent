@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short prefix for resource names.')
param prefix string = 'marco'

@description('Container App managed environment name.')
param managedEnvironmentName string = '${prefix}-cae'

@description('Container App name.')
param containerAppName string = '${prefix}-bot'

@description('Azure Container Registry name. Must be globally unique and alphanumeric.')
param acrName string

@description('Image repository name in ACR.')
param imageRepository string = 'marco-agent'

@description('Image tag to deploy on initial provisioning.')
param imageTag string = 'bootstrap'

@secure()
@description('Discord bot token.')
param discordBotToken string

@secure()
@description('Azure AI Foundry API key.')
param azureAiFoundryKey string

@description('Azure AI Foundry endpoint.')
param azureAiFoundryEndpoint string

@description('Azure AI Foundry API version.')
param azureAiFoundryApiVersion string = '2024-10-21'

@description('Cosmos endpoint (optional).')
param cosmosDbEndpoint string = ''

@secure()
@description('Cosmos key (optional).')
param cosmosDbKey string = ''

@description('Cosmos database name.')
param cosmosDbDatabase string = 'marco'

@description('Cosmos memory container name.')
param cosmosDbContainer string = 'conversation_memory'

@description('Cosmos tasks container name.')
param cosmosTasksContainer string = 'tasks'

var logAnalyticsName = '${prefix}-law'
var appInsightsName = '${prefix}-appi'
var containerImage = '${acr.properties.loginServer}/${imageRepository}:${imageTag}'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: managedEnvironmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: listKeys(logAnalytics.id, logAnalytics.apiVersion).primarySharedKey
      }
    }
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-01-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
  }
}

resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: containerAppName
  location: location
  properties: {
    managedEnvironmentId: managedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
        {
          name: 'discord-bot-token'
          value: discordBotToken
        }
        {
          name: 'azure-ai-foundry-key'
          value: azureAiFoundryKey
        }
        {
          name: 'cosmos-db-key'
          value: cosmosDbKey
        }
      ]
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'marco'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'PORT'
              value: '8080'
            }
            {
              name: 'DISCORD_BOT_TOKEN'
              secretRef: 'discord-bot-token'
            }
            {
              name: 'AZURE_AI_FOUNDRY_ENDPOINT'
              value: azureAiFoundryEndpoint
            }
            {
              name: 'AZURE_AI_FOUNDRY_KEY'
              secretRef: 'azure-ai-foundry-key'
            }
            {
              name: 'AZURE_AI_FOUNDRY_API_VERSION'
              value: azureAiFoundryApiVersion
            }
            {
              name: 'COSMOS_DB_ENDPOINT'
              value: cosmosDbEndpoint
            }
            {
              name: 'COSMOS_DB_KEY'
              secretRef: 'cosmos-db-key'
            }
            {
              name: 'COSMOS_DB_DATABASE'
              value: cosmosDbDatabase
            }
            {
              name: 'COSMOS_DB_CONTAINER'
              value: cosmosDbContainer
            }
            {
              name: 'COSMOS_TASKS_CONTAINER'
              value: cosmosTasksContainer
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8080
              }
              initialDelaySeconds: 20
              periodSeconds: 15
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/healthz'
                port: 8080
              }
              initialDelaySeconds: 10
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

output resourceGroupName string = resourceGroup().name
output locationOut string = location
output acrNameOut string = acr.name
output acrLoginServer string = acr.properties.loginServer
output containerAppNameOut string = containerApp.name
output managedEnvironmentNameOut string = managedEnvironment.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
