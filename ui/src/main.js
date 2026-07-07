// ui/src/main.js — UI.2b (ui-6) entry point.
//
// Wires the extracted modules together and replaces the old 979-line IIFE.
// Owns the DOM event handlers for upload, sessions, extraction, save/approve/
// push — delegating rendering to render.js, polling to polling.js, the settings
// modal to modals.js, network to api.js, and shared state to state.js.
import {
  $activeSessionId,
  $taskData,
  setActiveSession,
  clearActiveSession,
  setTaskData,
  resetTaskData,
} from './state.js'
import {
  fetchSessions,
  fetchTasks,
  fetchStatus,
  fetchSettings,
  postTask,
  approveAll,
  uploadFile,
  startProcess,
  pushToJira,
  cancelRun,
  deleteSession,
  computeStats,
  pushPreflight,
  decideSessionSwitch,
  TASK_STATUS,
} from './api.js'
import {
  getEl,
  showToast,
  updateStats,
  renderTasks,
  markTasksLoaded,
  patchTaskCard,
  resolveTriageAction,
  isTypingTarget,
  triageCard,
  moveCardFocus,
  renderPushResults,
} from './render.js'
import { startStatusPolling, stopPolling, showProgressOverlay, errorRecovery } from './polling.js'
import { initSettingsModal, showConfirm } from './modals.js'
import { mountLoader } from './loaders.js'

const LAST_SESSION_KEY = 'lastViewedSessionId' // ui-17: persists across refresh, independent of the running flag.

document.addEventListener('DOMContentLoaded', () => {
  console.log('Impeccable UI Initialized')

  let selectedFile = null

  // ui-16: track the LAST operation so a push-failure Retry re-runs the PUSH,
  // not the extraction. `lastPushRequest` carries the exact payload to re-send.
  let lastOp = null // 'extraction' | 'push'
  let lastPushRequest = null

  // ui-19: Jira-configured flag (from /api/settings), gates the Push pre-flight.
  let jiraConfigured = false

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
  // beUI-ported "working" loader in the run overlay — pure-CSS Newton's cradle
  // (animates only while the overlay is visible; CSS pauses it under display:none).
  const progressLoaderMount = getEl('progressLoader')
  if (progressLoaderMount && !progressLoaderMount.childElementCount) {
    mountLoader(progressLoaderMount, 'newton', { label: 'Extracting' })
  }
  const logConsole = getEl('logConsole')
  const btnCancelProcess = getEl('btnCancelProcess')
  const btnDismissProgress = getEl('btnDismissProgress')
  const btnDeleteSession = getEl('btnDeleteSession')
  const sortConfidence = getEl('sortConfidence')

  // Filters + sort re-render the task list on toggle.
  ;[
    getEl('filterPending'),
    getEl('filterApproved'),
    getEl('filterRejected'),
    getEl('filterPushed'),
    getEl('filterFlagged'),
    getEl('sortConfidence'),
  ]
    .filter(Boolean)
    .forEach((el) => el.addEventListener('change', () => renderTasks(saveTask)))

  // Single dark theme (UI.1): no theme toggle, no data-theme.

  // UI.3 — error_class-aware failure recovery callbacks, consumed in the shared
  // polling error branch (TRANSIENT→Retry, USER_FIXABLE→Open Settings,
  // TERMINAL→Dismiss). ui-16: onRetry is now OP-AWARE — a push failure re-pushes,
  // an extraction failure re-extracts, so Retry never no-ops on the wrong op.
  const pollRecovery = {
    onRetry: () => {
      if (lastOp === 'push') retryPush()
      else startExtraction()
    },
    onOpenSettings: () => getEl('btnSettings')?.click(),
    onDismiss: () => {
      if (progressOverlay) progressOverlay.style.display = 'none'
      resetToNewSession()
    },
  }

  initSettingsModal()

  // Load Jira-configured state up front so the Push pre-flight is accurate before
  // the first push attempt. Re-checked opportunistically after settings save.
  refreshJiraConfigured()

  async function refreshJiraConfigured() {
    try {
      const s = await fetchSettings()
      // Server returns "***" for a stored token; empty string when unset.
      jiraConfigured = !!(s && s.jira_server_url && s.jira_api_token)
    } catch (e) {
      jiraConfigured = false
    }
    updatePushPreflight()
  }

  // --- ui-16: Push pre-flight (enable/disable + tooltip) --------------------
  function updatePushPreflight() {
    const btnPushJira = getEl('btnPushJira')
    if (!btnPushJira) return
    const approvedCount = computeStats(currentTasks()).approved
    const { enabled, reason } = pushPreflight({
      activeSessionId: $activeSessionId.get(),
      approvedCount,
      jiraConfigured,
    })
    btnPushJira.disabled = !enabled
    btnPushJira.title = enabled ? 'Push approved tasks to Jira' : reason
    btnPushJira.setAttribute('aria-disabled', String(!enabled))
  }

  function currentTasks() {
    return $taskData.get().tasks || []
  }

  // --- Upload ---------------------------------------------------------------
  // ui-19: shared validation for BOTH the change AND the drop handler (drop had
  // none). Rejects non-PDFs, wires the clear affordance, enables Start.
  function isPdf(file) {
    if (!file) return false
    const name = (file.name || '').toLowerCase()
    return file.type === 'application/pdf' || name.endsWith('.pdf')
  }

  function acceptFile(file) {
    if (!file) return
    if (!isPdf(file)) {
      showToast('Only PDF files are supported (.pdf)', 'error')
      return
    }
    selectedFile = file
    if (uploadLabel) {
      uploadLabel.textContent = file.name
      uploadLabel.classList.add('has-file')
    }
    showClearFileAffordance(true)
    if (processConfig) processConfig.style.display = 'flex'
    if (btnStartProcess) btnStartProcess.disabled = false
  }

  function clearSelectedFile() {
    selectedFile = null
    if (pdfUpload) pdfUpload.value = ''
    if (uploadLabel) {
      uploadLabel.textContent = 'Click or drop SOW PDF here'
      uploadLabel.classList.remove('has-file')
    }
    showClearFileAffordance(false)
    if (btnStartProcess) btnStartProcess.disabled = true
  }

  function showClearFileAffordance(show) {
    let btn = getEl('btnClearFile')
    if (!btn && show && uploadZone) {
      btn = document.createElement('button')
      btn.id = 'btnClearFile'
      btn.type = 'button'
      btn.className = 'btn-icon clear-file-btn'
      btn.setAttribute('aria-label', 'Clear selected file')
      btn.textContent = '×'
      btn.addEventListener('click', (e) => {
        e.stopPropagation()
        clearSelectedFile()
      })
      uploadZone.appendChild(btn)
    }
    if (btn) btn.style.display = show ? 'inline-flex' : 'none'
  }

  if (uploadZone && pdfUpload) {
    uploadZone.addEventListener('click', (e) => {
      if (e.target.id === 'btnClearFile') return
      pdfUpload.click()
    })

    pdfUpload.addEventListener('change', (e) => {
      if (e.target.files.length > 0) acceptFile(e.target.files[0])
    })

    uploadZone.addEventListener('dragover', (e) => {
      e.preventDefault()
      uploadZone.classList.add('drag-over')
    })
    uploadZone.addEventListener('dragleave', () => {
      uploadZone.classList.remove('drag-over')
    })
    uploadZone.addEventListener('drop', (e) => {
      e.preventDefault()
      uploadZone.classList.remove('drag-over')
      // ui-19: drop now validates the same as the file picker.
      if (e.dataTransfer.files.length > 0) acceptFile(e.dataTransfer.files[0])
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
      const ok = await showConfirm({
        title: 'Delete session?',
        message: 'This permanently removes the extracted tasks for this session.',
        confirmLabel: 'Delete',
        danger: true,
      })
      if (!ok) return
      try {
        const res = await deleteSession(idToDelete)
        if (res.ok) {
          showToast('Session deleted', 'success')
          clearActiveSession()
          clearLastViewed()
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
    setSessionsLoading(true)
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
      // ui-17: the switcher <select> mirrors the canonical store — no divergence.
      const active = $activeSessionId.get()
      if (active) sessionSwitcher.value = active
    } catch (e) {
      console.error('Failed to load sessions', e)
    } finally {
      setSessionsLoading(false)
    }
  }

  function resetToNewSession() {
    clearActiveSession()
    resetTaskData()
    clearPushResultsPanel()
    if (newExtractionContainer) newExtractionContainer.style.display = 'block'
    if (processConfig) processConfig.style.display = 'flex'
    clearSelectedFile()
    updateStats()
    renderTasks(saveTask)
    updatePushPreflight()
  }

  // ui-17/18: single canonical session-switch path. Mirrors the store, persists
  // the last-viewed session, and (ui-18) reopens progress when the target run is
  // still in flight instead of rendering a stale empty list.
  async function switchToSession(id) {
    if (!id) {
      resetToNewSession()
      return
    }
    setActiveSession(id)
    setLastViewed(id)
    if (sessionSwitcher) sessionSwitcher.value = id
    if (newExtractionContainer) newExtractionContainer.style.display = 'none'
    clearPushResultsPanel()

    try {
      const status = await fetchStatus(id)
      // Guard: user may have switched again while this was in flight.
      if ($activeSessionId.get() !== id) return
      if (decideSessionSwitch(status) === 'resume') {
        // ui-18: it's running — reopen the overlay + resume polling.
        lastOp = status.kind === 'jira_push' ? 'push' : 'extraction'
        showProgressOverlay()
        if (logConsole) logConsole.innerHTML = ''
        startStatusPolling(loadData, pollRecovery)
        return
      }
    } catch (e) {
      // Status probe failed — fall through to a plain load rather than blocking.
    }
    if (uploadLabel) uploadLabel.textContent = 'Loading session…'
    await loadData()
  }

  if (sessionSwitcher) {
    sessionSwitcher.addEventListener('change', (e) => {
      switchToSession(e.target.value)
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

  // --- ui-14: keyboard triage ----------------------------------------------
  // Document-level, but SCOPED: only acts on the FOCUSED card, and NEVER while a
  // form control has focus (so typing a title never rejects the card).
  document.addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return
    // Don't hijack keys while a modal/overlay is open (settings, confirm,
    // progress) — its own focus trap owns the keyboard.
    if (isOverlayOpen()) return
    const action = resolveTriageAction({ key: e.key, isTyping: isTypingTarget(e.target) })
    if (!action) return

    if (action === 'next' || action === 'prev') {
      const moved = moveCardFocus(action, document.activeElement)
      if (moved) e.preventDefault()
      return
    }
    // approve / reject / expand act on the focused card.
    const card = document.activeElement?.closest?.('.task-card')
    if (!card) return
    if (triageCard(action, card)) e.preventDefault()
  })

  // --- Extraction -----------------------------------------------------------
  async function startExtraction() {
    if (!selectedFile) return
    lastOp = 'extraction'
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
        setLastViewed(data.run_id)

        showProgressOverlay()
        if (logConsole) logConsole.innerHTML = ''

        await loadSessions()
        if (sessionSwitcher) sessionSwitcher.value = $activeSessionId.get()
        if (newExtractionContainer) newExtractionContainer.style.display = 'none'

        startStatusPolling(loadData, pollRecovery)
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
    setTaskListLoading(true)
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
          uploadLabel.textContent = `Loaded from ${data.run_id || 'previous run'}`
          uploadLabel.classList.remove('has-file')
        }
      }

      updateStats()
      renderTasks(saveTask)
      updatePushPreflight()

      // ui-23: if this session's tasks include PUSHED items, surface the results
      // panel (clickable Jira links). Reconstructed from the reloaded task data +
      // env_defaults.jira_server (the only place the server exposes the base URL).
      maybeRenderPushResults(data)
    } catch (e) {
      markTasksLoaded() // a failed load still resolves the empty-state.
      showToast('Failed to load data', 'error')
    } finally {
      setTaskListLoading(false)
    }
  }

  // --- ui-23: push results panel -------------------------------------------
  // Called after a data reload. Only builds a panel when at least one PUSHED task
  // is present (i.e. a push actually happened for this session). The aggregate
  // failure info rides the polling status; we capture the last push failure here.
  let lastPushFailure = null // { failedCount, firstError, errorClass }
  function maybeRenderPushResults(data) {
    const tasks = (data && data.tasks) || []
    const pushed = tasks.filter((t) => t.status === TASK_STATUS.PUSHED && t.jira_issue_key)
    if (pushed.length === 0 && !lastPushFailure) {
      clearPushResultsPanel()
      return
    }
    const jiraServerUrl =
      (data.env_defaults && data.env_defaults.jira_server) || ''
    const fail = lastPushFailure || {}
    const chip = fail.failedCount ? mapFailureChip(fail.errorClass) : undefined
    renderPushResults({
      tasks,
      jiraServerUrl,
      failedCount: fail.failedCount || 0,
      firstError: fail.firstError || '',
      errorChip: chip,
    })
  }

  function mapFailureChip(errorClass) {
    const rec = errorRecovery(errorClass)
    return { label: rec.chipLabel, className: rec.chipClass }
  }

  function clearPushResultsPanel() {
    const p = getEl('pushResultsPanel')
    if (p) p.remove()
    lastPushFailure = null
  }

  // --- Save / approve / push ------------------------------------------------
  async function saveTask(updatedTask) {
    try {
      const res = await postTask($activeSessionId.get(), updatedTask)
      if (res.ok) {
        showToast('Task updated successfully', 'success')
        patchTaskCard(updatedTask, saveTask) // optimistic in-place patch (UI.4a ui-11)
        updatePushPreflight() // approved-count may have changed → re-gate Push.
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
      // ui-14: styled (non-native) confirm replaces window.confirm.
      const pending = computeStats(currentTasks()).pending
      const ok = await showConfirm({
        title: 'Approve all pending tasks?',
        message: pending
          ? `This approves ${pending} pending task(s) in this session.`
          : 'This approves all pending tasks in this session.',
        confirmLabel: 'Approve All',
      })
      if (!ok) return
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
      // ui-16: pre-flight guard. If disabled, do nothing (button is already
      // disabled, but guard defensively). Then confirm before pushing.
      const approvedCount = computeStats(currentTasks()).approved
      const preflight = pushPreflight({
        activeSessionId: $activeSessionId.get(),
        approvedCount,
        jiraConfigured,
      })
      if (!preflight.enabled) {
        showToast(preflight.reason, 'warning')
        return
      }
      const projectKey = getEl('projectKey')?.value || 'PROJ'
      const ok = await showConfirm({
        title: 'Push to Jira?',
        message: `Push ${approvedCount} approved task(s) to ${projectKey}?`,
        confirmLabel: 'Push',
      })
      if (!ok) return

      const req = {
        jira_hierarchy: getEl('jiraHierarchy')?.value || 'epic_task',
        jira_project_key: projectKey,
      }
      await doPush(req)
    })
  }

  // ui-16: the actual push, factored out so the op-aware Retry can re-invoke it
  // with the SAME request. Tracks lastOp='push' + lastPushRequest.
  async function doPush(req) {
    lastOp = 'push'
    lastPushRequest = req
    clearPushResultsPanel()
    const btn = getEl('btnPushJira')
    if (btn) {
      btn.disabled = true
      btn.textContent = 'Pushing…'
    }
    try {
      const data = await pushToJira($activeSessionId.get(), req)
      if (data.started && data.run_id) {
        setActiveSession(data.run_id)
        setLastViewed(data.run_id)
        showProgressOverlay()
        if (logConsole) logConsole.innerHTML = ''
        showToast(data.message || 'Jira push started', 'success')
        startStatusPolling(onPushComplete, pollRecovery)
      } else if (data.success) {
        // Synchronous success is not the normal path, but handle it.
        showToast('Pushed to Jira Successfully!', 'success')
        await loadData()
      } else {
        // Absorbed pre-flight case; shouldn't normally reach here after the guard.
        showToast(data.message || 'Push failed', 'error')
      }
    } catch (e) {
      showToast('Failed to contact server', 'error')
    } finally {
      if (btn) {
        btn.textContent = 'Push to Jira'
        updatePushPreflight()
      }
    }
  }

  async function retryPush() {
    if (lastPushRequest) {
      await doPush(lastPushRequest)
      return
    }
    // Resumed/refreshed push with no cached payload: the polling failure branch
    // never ran loadData, so the store is empty and the Push button is disabled
    // by pre-flight — clicking it would be a silent no-op. Reload the session
    // first (restores tasks + config AND re-enables the button via
    // updatePushPreflight), then re-push if there are approved tasks (the backend
    // push is idempotent — a task with a jira_issue_key is skipped). Otherwise
    // guide the user instead of failing silently.
    if (progressOverlay) progressOverlay.style.display = 'none'
    await loadData()
    const approvedCount = computeStats(currentTasks()).approved
    if (approvedCount > 0) {
      await doPush({
        jira_hierarchy: getEl('jiraHierarchy')?.value || 'epic_task',
        jira_project_key: getEl('projectKey')?.value || 'PROJ',
      })
    } else {
      showToast('Session reloaded — review tasks, then push again.', 'warning')
    }
  }

  // ui-23: when a push run completes, capture any aggregate failure from the
  // final status, then reload data (which renders the results panel).
  async function onPushComplete() {
    try {
      const status = await fetchStatus($activeSessionId.get())
      if (status && status.error) {
        // Parse "N passed, M failed" from the message when present.
        const m = /(\d+)\s+failed/.exec(status.message || '')
        lastPushFailure = {
          failedCount: m ? parseInt(m[1], 10) : 1,
          firstError: status.error,
          errorClass: status.error_class,
        }
      } else {
        lastPushFailure = null
      }
    } catch (e) {
      lastPushFailure = null
    }
    await loadData()
  }

  // --- ui-19: loading states -----------------------------------------------
  function setTaskListLoading(on) {
    const taskList = getEl('taskList')
    if (!taskList) return
    if (on) {
      taskList.setAttribute('aria-busy', 'true')
      if (!taskList.querySelector('.skeleton-list')) {
        const sk = document.createElement('div')
        sk.className = 'skeleton-list'
        sk.setAttribute('aria-hidden', 'true')
        sk.innerHTML =
          '<div class="skeleton-card"></div><div class="skeleton-card"></div><div class="skeleton-card"></div>'
        // Only show skeleton when the list is currently empty (first load) to
        // avoid flashing over already-rendered content on a refresh.
        if (taskList.children.length === 0) taskList.appendChild(sk)
      }
    } else {
      taskList.removeAttribute('aria-busy')
      taskList.querySelector('.skeleton-list')?.remove()
    }
  }

  function setSessionsLoading(on) {
    if (!sessionSwitcher) return
    sessionSwitcher.setAttribute('aria-busy', String(!!on))
    sessionSwitcher.classList.toggle('is-loading', !!on)
  }

  // --- ui-17: last-viewed session persistence (independent of running flag) --
  function setLastViewed(id) {
    try {
      if (id) localStorage.setItem(LAST_SESSION_KEY, id)
    } catch (e) {
      /* localStorage may be unavailable (private mode) — non-fatal. */
    }
  }
  function getLastViewed() {
    try {
      return localStorage.getItem(LAST_SESSION_KEY)
    } catch (e) {
      return null
    }
  }
  function clearLastViewed() {
    try {
      localStorage.removeItem(LAST_SESSION_KEY)
    } catch (e) {
      /* non-fatal */
    }
  }

  // Resume polling when the tab becomes focused.
  document.addEventListener('visibilitychange', async () => {
    if (document.visibilityState === 'visible' && $activeSessionId.get()) {
      console.log('Tab focused, resuming status sync...')
      // ui-16: if we don't yet know the op-type (e.g. focus fired before a
      // resume set it), recover it so a push failure's Retry re-pushes.
      if (!lastOp) {
        try {
          const status = await fetchStatus($activeSessionId.get())
          lastOp = status.kind === 'jira_push' ? 'push' : 'extraction'
        } catch (e) {
          /* leave lastOp null; retryPush() still falls back to the push button */
        }
      }
      startStatusPolling(loadData, pollRecovery)
    }
  })

  // Auto-resume for refreshes / tabbing back into a suspended tab.
  async function autoResumeSession() {
    const storedId = sessionStorage.getItem('activeSessionId')
    if (!storedId) return
    console.log('Auto-resuming session:', storedId)
    setActiveSession(storedId)
    showProgressOverlay()
    if (newExtractionContainer) newExtractionContainer.style.display = 'none'
    // ui-16: recover the op-type from the RUNNING run so a push failure's Retry
    // re-pushes (not re-extracts). A fresh page load resets lastOp=null; without
    // this, a mid-push refresh + transient failure would make Retry silently
    // no-op on the wrong op. Mirrors switchToSession's resume branch.
    let onDone = loadData
    try {
      const status = await fetchStatus(storedId)
      lastOp = status.kind === 'jira_push' ? 'push' : 'extraction'
      if (lastOp === 'push') onDone = onPushComplete
    } catch (e) {
      /* keep lastOp as-is; a plain poll still recovers the run state */
    }
    startStatusPolling(onDone, pollRecovery)
  }

  // Initialize.
  loadSessions().then(async () => {
    await autoResumeSession()
    if (sessionStorage.getItem('activeSessionId')) return

    // ui-17: a mid-review refresh stays put — restore the last-viewed session
    // (persisted independently of the running flag). If it's gone, fall back to
    // a plain load of the default (which renders the first-run state).
    const lastViewed = getLastViewed()
    if (lastViewed && sessionExists(lastViewed)) {
      await switchToSession(lastViewed)
    } else {
      await loadData()
    }
  })

  function sessionExists(id) {
    if (!sessionSwitcher) return false
    return Array.from(sessionSwitcher.options).some((o) => o.value === id)
  }

  // True when any overlay-style modal is visibly displayed (settings, confirm,
  // progress) — used to suspend keyboard triage so the modal's trap owns keys.
  function isOverlayOpen() {
    return Array.from(document.querySelectorAll('.progress-overlay')).some(
      (el) => el.style.display && el.style.display !== 'none'
    )
  }
})
