// ui/src/modals.js — UI.2b (ui-6)
//
// Settings modal: provider registry, provider-specific field toggling, debounced
// model discovery, and save. Self-contained — the only cross-module deps are the
// api fetch adapters and the toast/getEl view helpers.
import {
  fetchProviders,
  fetchSettings,
  saveSettings,
  fetchModels as apiFetchModels,
} from './api.js'
import { getEl, showToast } from './render.js'

let providerRegistry = {}
let providerSettingsCache = {}
let modelFetchTimer = null

async function loadProviders() {
  providerRegistry = await fetchProviders()
  const providerSelect = getEl('providerSelect')
  if (providerSelect) {
    providerSelect.innerHTML = ''
    Object.keys(providerRegistry).forEach((key) => {
      const opt = document.createElement('option')
      opt.value = key
      opt.textContent = key
      providerSelect.appendChild(opt)
    })
  }
}

function updateProviderUI() {
  const providerSelect = getEl('providerSelect')
  const provider = providerSelect?.value
  const reg = providerRegistry[provider] || {}
  const cached = providerSettingsCache[provider] || {}

  const baseUrlGroup = getEl('baseUrlGroup')
  const providerBaseUrl = getEl('providerBaseUrl')
  const azureFields = getEl('azureFields')
  const openrouterModelHelp = getEl('openrouterModelHelp')
  const apiKeyGroup = getEl('apiKeyGroup')
  const providerApiKey = getEl('providerApiKey')
  const providerModel = getEl('providerModelSelect')
  const providerModelSelect = getEl('providerModelSelect')
  const modelFetchStatus = getEl('modelFetchStatus')
  const azureDeploymentName = getEl('azureDeploymentName')
  const azureApiVersion = getEl('azureApiVersion')

  if (baseUrlGroup) baseUrlGroup.style.display = reg.show_base_url ? 'block' : 'none'
  if (providerBaseUrl) providerBaseUrl.value = cached.base_url || reg.base_url || ''
  if (azureFields) azureFields.style.display = provider === 'azure' ? 'block' : 'none'
  if (openrouterModelHelp)
    openrouterModelHelp.style.display = provider === 'openrouter' ? 'block' : 'none'
  if (apiKeyGroup) apiKeyGroup.style.display = provider === 'ollama' ? 'none' : 'block'
  if (providerApiKey) {
    if (cached.api_key && cached.api_key !== '***') {
      providerApiKey.value = cached.api_key
    } else {
      providerApiKey.value = ''
      providerApiKey.placeholder = cached.api_key ? 'Stored (re-enter to change)' : 'sk-...'
    }
  }
  if (providerModel) providerModel.value = cached.model || ''
  if (azureDeploymentName) azureDeploymentName.value = cached.azure_deployment_name || ''
  if (azureApiVersion) azureApiVersion.value = cached.azure_api_version || ''
  if (providerModelSelect) providerModelSelect.innerHTML = ''
  if (modelFetchStatus) {
    modelFetchStatus.textContent = ''
    modelFetchStatus.style.color = 'var(--text-tertiary)'
  }
}

async function fetchModels() {
  const providerSelect = getEl('providerSelect')
  if (!providerSelect) return
  const provider = providerSelect.value
  if (!provider) return

  const providerApiKey = getEl('providerApiKey')
  const providerBaseUrl = getEl('providerBaseUrl')
  const azureDeploymentName = getEl('azureDeploymentName')
  const azureApiVersion = getEl('azureApiVersion')
  const providerModelSelect = getEl('providerModelSelect')
  const modelFetchStatus = getEl('modelFetchStatus')

  if (modelFetchStatus) {
    modelFetchStatus.textContent = 'Fetching...'
    modelFetchStatus.style.color = 'var(--text-secondary)'
  }
  try {
    const payload = {
      api_key: providerApiKey?.value,
      base_url: providerBaseUrl?.value,
      azure_deployment_name: azureDeploymentName?.value,
      azure_api_version: azureApiVersion?.value,
    }
    const models = await apiFetchModels(provider, payload)
    if (providerModelSelect) {
      providerModelSelect.innerHTML = ''
      models.forEach((m) => {
        const opt = document.createElement('option')
        opt.value = m
        opt.textContent = m
        providerModelSelect.appendChild(opt)
      })

      // Restore from cache if available, otherwise fall back to first model.
      const cachedModel = providerSettingsCache[provider]?.model
      if (cachedModel && models.includes(cachedModel)) {
        providerModelSelect.value = cachedModel
      } else if (models.length > 0) {
        providerModelSelect.value = models[0]
      }
    }
    if (modelFetchStatus) {
      modelFetchStatus.textContent = `✓ ${models.length} models`
      modelFetchStatus.style.color = 'var(--success)'
    }
  } catch (e) {
    if (modelFetchStatus) {
      modelFetchStatus.textContent = `✗ ${e.message || 'Fetch failed'}`
      modelFetchStatus.style.color = 'var(--error)'
    }
  }
}

function scheduleModelFetch() {
  if (modelFetchTimer) clearTimeout(modelFetchTimer)
  modelFetchTimer = setTimeout(fetchModels, 400)
}

export function initSettingsModal() {
  const btnSettings = getEl('btnSettings')
  const settingsModal = getEl('settingsModal')
  const btnCancelSettings = getEl('btnCancelSettings')
  const btnSaveSettings = getEl('btnSaveSettings')
  const providerSelect = getEl('providerSelect')
  const providerApiKey = getEl('providerApiKey')
  const providerBaseUrl = getEl('providerBaseUrl')
  const providerModel = getEl('providerModelSelect')
  const providerModelSelect = getEl('providerModelSelect')
  const btnFetchModels = getEl('btnFetchModels')
  const modelFetchStatus = getEl('modelFetchStatus')
  const azureDeploymentName = getEl('azureDeploymentName')
  const azureApiVersion = getEl('azureApiVersion')

  if (btnSettings && settingsModal) {
    btnSettings.addEventListener('click', async () => {
      try {
        await loadProviders()
        const data = await fetchSettings()
        providerSettingsCache = data.providers || {}
        if (providerSelect) providerSelect.value = data.provider || 'openai'
        if (getEl('jiraServerUrl')) getEl('jiraServerUrl').value = data.jira_server_url || ''
        if (getEl('jiraApiToken')) getEl('jiraApiToken').value = data.jira_api_token || ''
        updateProviderUI()
        settingsModal.style.display = 'flex'
        scheduleModelFetch()
      } catch (e) {
        showToast('Failed to load settings', 'error')
      }
    })

    btnCancelSettings.addEventListener('click', () => {
      settingsModal.style.display = 'none'
    })

    btnSaveSettings.addEventListener('click', async () => {
      const payload = {
        provider: providerSelect?.value,
        model: providerModel?.value,
        api_key: providerApiKey?.value,
        base_url: providerBaseUrl?.value,
        azure_deployment_name: azureDeploymentName?.value,
        azure_api_version: azureApiVersion?.value,
        jira_server_url: getEl('jiraServerUrl')?.value,
        jira_api_token: getEl('jiraApiToken')?.value,
      }
      btnSaveSettings.textContent = 'Saving...'
      try {
        const res = await saveSettings(payload)
        if (res.ok) {
          const provider = providerSelect?.value
          if (provider) {
            providerSettingsCache[provider] = {
              model: providerModel?.value || '',
              api_key: providerApiKey?.value
                ? '***'
                : providerSettingsCache[provider]?.api_key || '',
              base_url: providerBaseUrl?.value || '',
              azure_deployment_name: azureDeploymentName?.value || '',
              azure_api_version: azureApiVersion?.value || '',
            }
          }
          showToast('Settings saved successfully!', 'success')
          settingsModal.style.display = 'none'
        } else {
          showToast('Failed to save settings', 'error')
        }
      } catch (e) {
        showToast('Connection error', 'error')
      } finally {
        btnSaveSettings.textContent = 'Save Settings'
      }
    })
  }

  if (providerSelect)
    providerSelect.addEventListener('change', () => {
      updateProviderUI()
      scheduleModelFetch()
    })
  if (providerModelSelect)
    providerModelSelect.addEventListener('change', () => {
      if (providerModel) providerModel.value = providerModelSelect.value
    })
  if (providerApiKey) providerApiKey.addEventListener('input', scheduleModelFetch)
  if (providerBaseUrl) providerBaseUrl.addEventListener('input', scheduleModelFetch)
  if (azureDeploymentName) azureDeploymentName.addEventListener('input', scheduleModelFetch)
  if (azureApiVersion) azureApiVersion.addEventListener('input', scheduleModelFetch)
  if (btnFetchModels) btnFetchModels.addEventListener('click', fetchModels)

  // "Use it" — set the OpenRouter recommended model directly. Works whether the
  // dropdown has been fetched or is empty; appends the option if missing.
  const btnUseRecommended = getEl('btnUseRecommendedModel')
  if (btnUseRecommended) {
    btnUseRecommended.addEventListener('click', () => {
      const recommended = 'google/gemini-2.5-flash'
      if (!providerModelSelect) return
      const existing = Array.from(providerModelSelect.options).find((o) => o.value === recommended)
      if (!existing) {
        const opt = document.createElement('option')
        opt.value = recommended
        opt.textContent = recommended
        providerModelSelect.appendChild(opt)
      }
      providerModelSelect.value = recommended
      if (modelFetchStatus) {
        modelFetchStatus.textContent = 'Set to recommended'
        modelFetchStatus.style.color = 'var(--success, #4caf50)'
      }
    })
  }
}
