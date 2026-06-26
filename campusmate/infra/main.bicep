// CampusMate — Azure AI Foundry infrastructure
// Scope: resource group
// Provisions: Foundry account + project, gpt-5 deployment, ACR (with project
// connection + AcrPull role), Storage, App Insights, Log Analytics.
// AI Search omitted (unused by either agent; add back if MCP Toolbox grounding needed).

targetScope = 'resourceGroup'

@description('Environment name (used for tagging and unique token).')
param environmentName string

@description('Azure region. Must support Foundry Hosted Agents (eastus2 recommended).')
param location string = resourceGroup().location

@description('Name of the Foundry project to create.')
param projectName string = 'campusmate'

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------
var abbrs         = loadJsonContent('./abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags          = { 'azd-env-name': environmentName }
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

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
// Azure AI Foundry account (CognitiveServices AIServices)
// allowProjectManagement: true is required to create child projects.
// ---------------------------------------------------------------------------
resource foundryAccount 'Microsoft.CognitiveServices/accounts@2024-10-01-preview' = {
  name: '${abbrs.cognitiveServicesAccounts}${resourceToken}'
  location: location
  tags: tags
  kind: 'AIServices'
  identity: { type: 'SystemAssigned' }
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: '${abbrs.cognitiveServicesAccounts}${resourceToken}'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
    allowProjectManagement: true
  }
}

// ---------------------------------------------------------------------------
// gpt-5 model deployment (child of the Foundry account)
// ---------------------------------------------------------------------------
resource gpt5Deployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  name: 'gpt-5'
  parent: foundryAccount
  sku: {
    name: 'GlobalStandard'
    capacity: 10
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
// Foundry Project (child of the Foundry account)
// SystemAssigned identity is required — its principalId is used for AcrPull.
// ---------------------------------------------------------------------------
resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2024-10-01-preview' = {
  name: projectName
  parent: foundryAccount
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {}
  dependsOn: [ gpt5Deployment ]
}

// ---------------------------------------------------------------------------
// ACR → Project connection
// Registers the Container Registry with the Foundry project so the hosted
// agent infrastructure knows which registry to pull images from.
// ---------------------------------------------------------------------------
resource projectAcrConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2024-10-01-preview' = {
  name: 'acr-connection'
  parent: foundryProject
  properties: {
    category: 'ContainerRegistry'
    target: 'https://${containerRegistry.properties.loginServer}'
    authType: 'None'
    isSharedToAll: true
    metadata: {}
  }
}

// ---------------------------------------------------------------------------
// AcrPull role for the Foundry ACCOUNT identity (account-level pulls)
// ---------------------------------------------------------------------------
resource acrPullForAccount 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, foundryAccount.id, acrPullRoleId)
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: foundryAccount.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// AcrPull role for the Foundry PROJECT identity (hosted agent image pulls)
// ---------------------------------------------------------------------------
resource acrPullForProject 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, foundryProject.id, acrPullRoleId)
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: foundryProject.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// FOUNDRY_PROJECT_ENDPOINT: format the azd ai.agents extension expects.
// Project endpoint is always: https://<account>.services.ai.azure.com/api/projects/<project>
// ---------------------------------------------------------------------------
output AZURE_RESOURCE_GROUP string = resourceGroup().name
output AZURE_LOCATION string = location
output AZURE_AI_FOUNDRY_ACCOUNT_NAME string = foundryAccount.name
output AZURE_AI_PROJECT_NAME string = foundryProject.name
output AZURE_AI_PROJECT_ID string = foundryProject.id
output FOUNDRY_PROJECT_ENDPOINT string = 'https://${foundryAccount.name}.services.ai.azure.com/api/projects/${foundryProject.name}'
output AZURE_AI_SERVICES_ENDPOINT string = foundryAccount.properties.endpoint
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.properties.loginServer
output APPLICATIONINSIGHTS_CONNECTION_STRING string = appInsights.properties.ConnectionString
