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

// ---------------------------------------------------------------------------
// UI.6 (ui-21) — reusable WCAG dialog a11y layer.
//
// The markup we own is a `.progress-overlay` backdrop wrapping a
// `.progress-card` panel (see #settingsModal / #progressOverlay in index.html).
// These helpers set the ARIA dialog role on the *card*, trap focus inside it,
// move focus in on open, restore it on close, close on Escape (topmost first),
// and close on a backdrop click without swallowing inner clicks. They're kept
// self-contained + exported so any modal (settings, progress) can reuse them
// without new index.html markup — attributes are applied from JS.
// ---------------------------------------------------------------------------

// Elements that can hold keyboard focus inside a dialog.
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

// Stack of currently-open modal overlays so Escape only closes the topmost.
const modalStack = []
// Per-overlay bookkeeping (opener element, bound listeners, onClose hook).
const modalState = new WeakMap()

let escBound = false

// The panel we apply role="dialog" to. Prefer the inner card; fall back to the
// overlay itself if there's no card (keeps the helper robust for odd markup).
function panelOf(overlay) {
  return overlay.querySelector('.progress-card') || overlay
}

function isVisible(el) {
  if (!el) return false
  // offsetParent is null in jsdom, so rely on the display flag / disabled state.
  return !el.hasAttribute('disabled') && el.getAttribute('aria-hidden') !== 'true'
}

function focusableWithin(panel) {
  return Array.from(panel.querySelectorAll(FOCUSABLE_SELECTOR)).filter(isVisible)
}

export function isModalOpen(overlay) {
  return !!overlay && modalStack.includes(overlay)
}

function topmostModal() {
  return modalStack[modalStack.length - 1] || null
}

// Single document-level Escape handler; acts on the topmost open modal only.
function onDocumentKeydown(e) {
  if (e.key === 'Escape') {
    const top = topmostModal()
    if (top) {
      e.preventDefault()
      closeModal(top)
    }
    return
  }
  if (e.key === 'Tab') {
    const top = topmostModal()
    if (!top) return
    trapFocus(e, top)
  }
}

// Keep Tab / Shift+Tab cycling within the dialog panel.
function trapFocus(e, overlay) {
  const panel = panelOf(overlay)
  const focusables = focusableWithin(panel)
  if (focusables.length === 0) {
    // Nothing focusable — keep focus on the panel itself.
    e.preventDefault()
    panel.focus()
    return
  }
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  const active = document.activeElement

  if (e.shiftKey) {
    if (active === first || !panel.contains(active)) {
      e.preventDefault()
      last.focus()
    }
  } else if (active === last || !panel.contains(active)) {
    e.preventDefault()
    first.focus()
  }
}

/**
 * Open an overlay modal: mark ARIA, wire backdrop click, trap + move focus.
 * @param {HTMLElement} overlay - the `.progress-overlay` backdrop element.
 * @param {object} [opts]
 * @param {string} [opts.display='flex'] - display value used when shown.
 * @param {string} [opts.labelledBy] - id of the element labelling the dialog.
 * @param {() => void} [opts.onClose] - callback fired after close.
 */
export function openModal(overlay, opts = {}) {
  if (!overlay || isModalOpen(overlay)) return
  const panel = panelOf(overlay)
  const display = opts.display || 'flex'

  panel.setAttribute('role', 'dialog')
  panel.setAttribute('aria-modal', 'true')

  // Wire aria-labelledby: use the supplied id, else derive from the first
  // heading in the panel (assigning it a stable id if it lacks one).
  let labelId = opts.labelledBy
  if (!labelId) {
    const heading = panel.querySelector('h1, h2, h3')
    if (heading) {
      if (!heading.id) {
        heading.id = (overlay.id || 'modal') + '-title'
      }
      labelId = heading.id
    }
  }
  if (labelId) panel.setAttribute('aria-labelledby', labelId)

  // Guarded backdrop click — close only when the backdrop itself is the target.
  const onBackdropClick = (e) => {
    if (e.target === overlay) closeModal(overlay)
  }
  overlay.addEventListener('click', onBackdropClick)

  modalState.set(overlay, {
    opener: document.activeElement,
    onBackdropClick,
    onClose: opts.onClose,
  })

  overlay.style.display = display
  modalStack.push(overlay)

  if (!escBound) {
    document.addEventListener('keydown', onDocumentKeydown, true)
    escBound = true
  }

  // Move focus into the dialog: first focusable, else the panel itself.
  const focusables = focusableWithin(panel)
  if (focusables.length > 0) {
    focusables[0].focus()
  } else {
    if (!panel.hasAttribute('tabindex')) panel.setAttribute('tabindex', '-1')
    panel.focus()
  }
}

/**
 * Close an overlay modal: hide it, detach listeners, restore opener focus, and
 * invoke the onClose hook registered at open time.
 * @param {HTMLElement} overlay
 */
export function closeModal(overlay) {
  if (!overlay || !isModalOpen(overlay)) return
  const state = modalState.get(overlay) || {}

  overlay.style.display = 'none'
  if (state.onBackdropClick) overlay.removeEventListener('click', state.onBackdropClick)

  const idx = modalStack.indexOf(overlay)
  if (idx !== -1) modalStack.splice(idx, 1)

  if (modalStack.length === 0 && escBound) {
    document.removeEventListener('keydown', onDocumentKeydown, true)
    escBound = false
  }

  modalState.delete(overlay)

  // Restore focus to whatever was focused before we opened.
  const opener = state.opener
  if (opener && typeof opener.focus === 'function' && document.contains(opener)) {
    opener.focus()
  }

  if (typeof state.onClose === 'function') state.onClose()
}

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
        openModal(settingsModal)
        scheduleModelFetch()
      } catch (e) {
        showToast('Failed to load settings', 'error')
      }
    })

    btnCancelSettings.addEventListener('click', () => {
      closeModal(settingsModal)
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
          closeModal(settingsModal)
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
