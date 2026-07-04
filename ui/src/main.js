// ui/src/main.js — UI.2b (ui-6) entry point.
//
// Wires the extracted modules together and replaces the old 979-line IIFE.
// Owns the DOM event handlers for upload, sessions, extraction, save/approve/
// push — delegating rendering to render.js, polling to polling.js, the settings
// modal to modals.js, network to api.js, and shared state to state.js.
import {
  $activeSessionId,
  setActiveSession,
  clearActiveSession,
  setTaskData,
  resetTaskData,
} from './state.js'
import {
  fetchSessions,
  fetchTasks,
  postTask,
  approveAll,
  uploadFile,
  startProcess,
  pushToJira,
  cancelRun,
  deleteSession,
} from './api.js'
import { getEl, showToast, updateStats, renderTasks, markTasksLoaded } from './render.js'
import { startStatusPolling, stopPolling, showProgressOverlay } from './polling.js'
import { initSettingsModal } from './modals.js'

document.addEventListener('DOMContentLoaded', () => {
  console.log('Impeccable UI Initialized')

  let selectedFile = null

  // DOM elements used by the entry-level handlers.
  const uploadZone = getEl('uploadZone')
  const pdfUpload = getEl('pdfUpload')
  const uploadLabel = getEl('uploadLabel')
  const processConfig = getEl('processConfig')
  const btnStartProcess = getEl('btnStartProcess')

  const sessionSwitcher = getEl('sessionSwitcher')
  const btnNewSession = getEl('btnNewSession')
  const btnCollapseAll = getEl('btnCollapseAll')
  const newExtractionContainer = getEl('newExtractionContainer')

  const progressOverlay = getEl('progressOverlay')
  const logConsole = getEl('logConsole')
  const btnCancelProcess = getEl('btnCancelProcess')
  const btnDismissProgress = getEl('btnDismissProgress')
  const btnDeleteSession = getEl('btnDeleteSession')

  // Filters re-render the task list on toggle.
  ;[getEl('filterPending'), getEl('filterApproved'), getEl('filterRejected'), getEl('filterFlagged')]
    .filter(Boolean)
    .forEach((el) => el.addEventListener('change', () => renderTasks(saveTask)))

  // Single dark theme (UI.1): no theme toggle, no data-theme.

  initSettingsModal()

  // --- Upload ---------------------------------------------------------------
  if (uploadZone && pdfUpload) {
    uploadZone.addEventListener('click', () => pdfUpload.click())

    pdfUpload.addEventListener('change', (e) => {
      if (e.target.files.length > 0) {
        selectedFile = e.target.files[0]
        if (uploadLabel) {
          uploadLabel.textContent = `📄 ${selectedFile.name}`
          uploadLabel.style.color = 'var(--accent-primary)'
        }
        if (processConfig) processConfig.style.display = 'flex'
        if (btnStartProcess) btnStartProcess.disabled = false
      }
    })

    uploadZone.addEventListener('dragover', (e) => {
      e.preventDefault()
      uploadZone.style.borderColor = 'var(--accent-primary)'
    })
    uploadZone.addEventListener('dragleave', () => {
      uploadZone.style.borderColor = ''
    })
    uploadZone.addEventListener('drop', (e) => {
      e.preventDefault()
      uploadZone.style.borderColor = ''
      if (e.dataTransfer.files.length > 0) {
        selectedFile = e.dataTransfer.files[0]
        if (uploadLabel) uploadLabel.textContent = `📄 ${selectedFile.name}`
        if (processConfig) processConfig.style.display = 'flex'
        if (btnStartProcess) btnStartProcess.disabled = false
      }
    })
  }

  if (btnStartProcess) btnStartProcess.addEventListener('click', startExtraction)

  if (btnDismissProgress) {
    btnDismissProgress.addEventListener('click', () => {
      if (progressOverlay) progressOverlay.style.display = 'none'
    })
  }

  if (btnCancelProcess) {
    btnCancelProcess.addEventListener('click', async () => {
      const sessionToCancel = $activeSessionId.get()
      if (!sessionToCancel) return

      // 1. Immediate UI reset — NEVER block on the network. The backend may be
      //    the very thing that's hung, so tearing down the UI must not wait.
      stopPolling()
      if (progressOverlay) progressOverlay.style.display = 'none'
      showToast('Extraction Stopped', 'success')
      clearActiveSession()
      resetToNewSession()

      // 2. Signal the backend best-effort, bounded by a short timeout.
      try {
        const ac = new AbortController()
        const t = setTimeout(() => ac.abort(), 3000)
        await cancelRun(sessionToCancel, ac.signal)
        clearTimeout(t)
      } catch (e) {
        /* UI already reset; the run is abandoned client-side regardless. */
      }
      await loadSessions()
    })
  }

  if (btnDeleteSession) {
    btnDeleteSession.addEventListener('click', async () => {
      const idToDelete = sessionSwitcher ? sessionSwitcher.value : $activeSessionId.get()
      if (!idToDelete) {
        showToast('No session selected', 'warning')
        return
      }
      if (!confirm('Are you sure you want to delete this session?')) return
      try {
        const res = await deleteSession(idToDelete)
        if (res.ok) {
          showToast('Session deleted', 'success')
          clearActiveSession()
          await loadSessions()
          resetToNewSession()
        }
      } catch (e) {
        showToast('Failed to delete session', 'error')
      }
    })
  }

  // --- Sessions -------------------------------------------------------------
  async function loadSessions() {
    if (!sessionSwitcher) return
    try {
      const sessions = await fetchSessions()
      while (sessionSwitcher.options.length > 1) sessionSwitcher.remove(1)

      if (sessions && Array.isArray(sessions)) {
        sessions.forEach((s) => {
          const opt = document.createElement('option')
          opt.value = s.run_id
          const dateStr = s.created_at || new Date().toISOString()
          const d = new Date(dateStr).toLocaleString(undefined, {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
          })
          const label = s.filename || s.run_id
          opt.textContent = `${label} (${d})`
          sessionSwitcher.appendChild(opt)
        })
      }
      const active = $activeSessionId.get()
      if (active) sessionSwitcher.value = active
    } catch (e) {
      console.error('Failed to load sessions', e)
    }
  }

  function resetToNewSession() {
    clearActiveSession()
    resetTaskData()
    if (newExtractionContainer) newExtractionContainer.style.display = 'block'
    if (processConfig) processConfig.style.display = 'flex'
    if (btnStartProcess) btnStartProcess.disabled = true
    if (uploadLabel) {
      uploadLabel.textContent = 'Click or drop SOW PDF here'
      uploadLabel.style.color = ''
    }
    selectedFile = null
    updateStats()
    renderTasks(saveTask)
  }

  if (sessionSwitcher) {
    sessionSwitcher.addEventListener('change', (e) => {
      const id = e.target.value
      setActiveSession(id)
      if (id) {
        if (newExtractionContainer) newExtractionContainer.style.display = 'none'
        if (uploadLabel) uploadLabel.textContent = `📁 Loading session...`
        loadData()
      } else {
        resetToNewSession()
      }
    })
  }

  if (btnNewSession) {
    btnNewSession.addEventListener('click', () => {
      if (sessionSwitcher) sessionSwitcher.value = ''
      resetToNewSession()
    })
  }

  if (btnCollapseAll) {
    btnCollapseAll.addEventListener('click', () => {
      document.querySelectorAll('.task-group').forEach((group) => {
        group.classList.add('collapsed')
        const svg = group.querySelector('.group-chevron')
        if (svg) svg.style.transform = 'rotate(-90deg)'
      })
      document.querySelectorAll('.task-card').forEach((card) => {
        card.classList.remove('expanded')
        const svg = card.querySelector('.chevron')
        if (svg) svg.style.transform = ''
      })
      showToast('All items collapsed', 'success')
    })
  }

  // --- Extraction -----------------------------------------------------------
  async function startExtraction() {
    if (!selectedFile) return
    try {
      showToast('Uploading SOW...', 'success')
      const uploadData = await uploadFile(selectedFile)

      const req = {
        pdf_filename: uploadData.filename,
        llm_mode: getEl('llmMode')?.value || 'api',
        jira_hierarchy: getEl('jiraHierarchy')?.value || 'epic_task',
        jira_project_key: getEl('projectKey')?.value || 'PROJ',
        skip_indexing: false,
        max_nodes: parseInt(getEl('maxNodes')?.value || '200', 10),
      }

      const processRes = await startProcess(req)
      if (processRes.ok) {
        const data = await processRes.json()
        setActiveSession(data.run_id)

        showProgressOverlay()
        if (logConsole) logConsole.innerHTML = ''

        await loadSessions()
        if (sessionSwitcher) sessionSwitcher.value = $activeSessionId.get()
        if (newExtractionContainer) newExtractionContainer.style.display = 'none'

        startStatusPolling(loadData)
      } else {
        const err = await processRes.json()
        showToast(err.detail || 'Failed to start process', 'error')
      }
    } catch (e) {
      showToast('Connection error', 'error')
    }
  }

  // --- Data load ------------------------------------------------------------
  async function loadData() {
    try {
      const data = await fetchTasks($activeSessionId.get())
      setTaskData(data)
      markTasksLoaded()

      if (data.config || data.env_defaults) {
        const cfg = data.config || {}
        const env = data.env_defaults || {}

        if (getEl('llmMode')) getEl('llmMode').value = cfg.llm_mode || 'api'
        if (getEl('jiraHierarchy')) getEl('jiraHierarchy').value = cfg.jira_hierarchy || 'epic_task'
        if (getEl('maxNodes')) getEl('maxNodes').value = cfg.max_nodes || 200
        if (getEl('projectKey')) {
          getEl('projectKey').value = env.jira_project_key || cfg.jira_project_key || 'PROJ'
        }

        if (processConfig) processConfig.style.display = 'flex'
        if (uploadLabel && !selectedFile) {
          uploadLabel.textContent = `📁 Loaded from ${data.run_id || 'previous run'}`
          uploadLabel.style.color = 'var(--text-secondary)'
        }
      }

      updateStats()
      renderTasks(saveTask)
    } catch (e) {
      markTasksLoaded() // a failed load still resolves the empty-state.
      showToast('Failed to load data', 'error')
    }
  }

  // --- Save / approve / push ------------------------------------------------
  async function saveTask(updatedTask) {
    try {
      const res = await postTask($activeSessionId.get(), updatedTask)
      if (res.ok) {
        showToast('Task updated successfully', 'success')
        await loadData()
      } else {
        throw new Error()
      }
    } catch (e) {
      showToast('Failed to update task', 'error')
    }
  }

  const btnApproveAll = getEl('btnApproveAll')
  if (btnApproveAll) {
    btnApproveAll.addEventListener('click', async () => {
      try {
        const data = await approveAll($activeSessionId.get())
        showToast(data.message, 'success')
        await loadData()
      } catch (e) {
        showToast('Failed to approve all', 'error')
      }
    })
  }

  const btnPushJira = getEl('btnPushJira')
  if (btnPushJira) {
    btnPushJira.addEventListener('click', async () => {
      btnPushJira.disabled = true
      const originalText = btnPushJira.textContent
      btnPushJira.textContent = '⏳ Pushing...'

      const req = {
        jira_hierarchy: getEl('jiraHierarchy')?.value || 'epic_task',
        jira_project_key: getEl('projectKey')?.value,
      }

      try {
        const data = await pushToJira($activeSessionId.get(), req)
        if (data.started && data.run_id) {
          setActiveSession(data.run_id)
          showProgressOverlay()
          if (logConsole) logConsole.innerHTML = ''
          showToast(data.message || 'Jira push started', 'success')
          startStatusPolling(loadData)
        } else if (data.success) {
          showToast('Pushed to Jira Successfully!', 'success')
          await loadData()
        } else {
          showToast(data.message || 'Push failed', 'error')
        }
      } catch (e) {
        showToast('Failed to contact server', 'error')
      } finally {
        btnPushJira.disabled = false
        btnPushJira.textContent = originalText
      }
    })
  }

  // Resume polling when the tab becomes focused.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && $activeSessionId.get()) {
      console.log('Tab focused, resuming status sync...')
      startStatusPolling(loadData)
    }
  })

  // Auto-resume for refreshes / tabbing back into a suspended tab.
  function autoResumeSession() {
    const storedId = sessionStorage.getItem('activeSessionId')
    if (storedId) {
      console.log('Auto-resuming session:', storedId)
      setActiveSession(storedId)
      showProgressOverlay()
      if (newExtractionContainer) newExtractionContainer.style.display = 'none'
      startStatusPolling(loadData)
    }
  }

  // Initialize.
  loadSessions().then(() => {
    autoResumeSession()
    if (!sessionStorage.getItem('activeSessionId')) {
      loadData()
    }
  })
})
