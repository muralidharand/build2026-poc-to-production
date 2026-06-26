// CampusMate — Azure infrastructure
// Deploys: AI Foundry account + project, gpt-5 model, ACR, App Insights,
// Log Analytics, AI Search, Bing grounding, Storage, capability host, connections.

targetScope = 'resourceGroup'

@description('The environment name (e.g. dev, prod).')
param environmentName string

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('The principal ID of the user/service principal running azd up.')
param principalId string = ''

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------
var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = { 'azd-env-name': environmentName }

// ---------------------------------------------------------------------------
// Log Analytics + App Insights
// ---------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${abbrs.insightsComponents}${resourceToken}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ---------------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------------
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${abbrs.storageStorageAccounts}${resourceToken}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

// ---------------------------------------------------------------------------
// Container Registry
// ---------------------------------------------------------------------------
resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${abbrs.containerRegistryRegistries}${resourceToken}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true }
}

// ---------------------------------------------------------------------------
// AI Search
// ---------------------------------------------------------------------------
resource aiSearch 'Microsoft.Search/searchServices@2024-03-01-preview' = {
  name: '${abbrs.searchSearchServices}${resourceToken}'
  location: location
  tags: tags
  sku: { name: 'basic' }
  properties: {
    replicaCount: 1
    partitionCount: 1
  }
}

// ---------------------------------------------------------------------------
// Azure AI Foundry account + project
// ---------------------------------------------------------------------------
resource aiAccount 'Microsoft.MachineLearningServices/workspaces@2024-10-01' = {
  name: '${abbrs.cognitiveServicesAccounts}${resourceToken}'
  location: location
  tags: tags
  kind: 'Hub'
  identity: { type: 'SystemAssigned' }
  properties: {
    storageAccount: storageAccount.id
    applicationInsights: appInsights.id
    containerRegistry: containerRegistry.id
  }
}

resource aiProject 'Microsoft.MachineLearningServices/workspaces@2024-10-01' = {
  name: '${abbrs.cognitiveServicesAccounts}proj-${resourceToken}'
  location: location
  tags: tags
  kind: 'Project'
  identity: { type: 'SystemAssigned' }
  properties: {
    hubResourceId: aiAccount.id
  }
}

// ---------------------------------------------------------------------------
// gpt-5 model deployment
// ---------------------------------------------------------------------------
resource gpt5Deployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  name: 'gpt-5'
  parent: any(aiAccount)
  sku: {
    name: 'GlobalStandard'
    capacity: 50
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5'
      version: '2025-08-07'
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
output AZURE_RESOURCE_GROUP string = resourceGroup().name
output AZURE_LOCATION string = location
output AZURE_AI_PROJECT_NAME string = aiProject.name
output AZURE_AI_HUB_NAME string = aiAccount.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.properties.loginServer
output APPLICATIONINSIGHTS_CONNECTION_STRING string = appInsights.properties.ConnectionString
