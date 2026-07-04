// ui/src/polling.js — UI.2b (ui-6)
//
// Status polling loop + progress-overlay chrome. Reads the active session from
// the store, tracks poll lifecycle in $pollStatus (was the old `statusInterval`
// + `pollErrorCount` locals). `onComplete` is injected by the entry module so
// polling doesn't import the data-load flow directly (avoids a cycle).
import { $activeSessionId, $pollStatus, setPollStatus, clearActiveSession } from './state.js'
import { fetchStatus } from './api.js'
import { getEl, showToast } from './render.js'

let statusInterval = null

export function showProgressOverlay() {
  const progressOverlay = getEl('progressOverlay')
  const btnCancelProcess = getEl('btnCancelProcess')
  const btnDismissProgress = getEl('btnDismissProgress')
  if (progressOverlay) progressOverlay.style.display = 'flex'
  if (btnCancelProcess) btnCancelProcess.style.display = 'inline-flex'
  if (btnDismissProgress) btnDismissProgress.style.display = 'none'
}

export function stopPolling() {
  if (statusInterval) clearInterval(statusInterval)
  statusInterval = null
  setPollStatus({ isRunning: false })
}

export function startStatusPolling(onComplete) {
  if (statusInterval) clearInterval(statusInterval)
  setPollStatus({ isRunning: true, pollErrorCount: 0 })

  const progressStepTitle = getEl('progressStepTitle')
  const progressMessage = getEl('progressMessage')
  const progressBarFill = getEl('progressBarFill')
  const progressPercentage = getEl('progressPercentage')
  const logConsole = getEl('logConsole')
  const btnCancelProcess = getEl('btnCancelProcess')
  const btnDismissProgress = getEl('btnDismissProgress')

  statusInterval = setInterval(async () => {
    const polledSession = $activeSessionId.get()
    try {
      const status = await fetchStatus(polledSession)
      // Stale-response guard: the user switched sessions mid-request → drop it.
      if ($activeSessionId.get() !== polledSession) return
      setPollStatus({ pollErrorCount: 0 })

      if (status.is_running || status.progress >= 1.0) {
        const runIdLabel = status.run_id ? ` (Run: ${status.run_id})` : ''
        if (progressStepTitle) {
          progressStepTitle.textContent =
            status.kind === 'jira_push'
              ? `Jira Push${runIdLabel}`
              : `Step ${status.current_step}${runIdLabel}`
        }
        if (progressMessage) progressMessage.textContent = status.message
        const pct = (status.progress * 100).toFixed(0)
        if (progressBarFill) progressBarFill.style.width = `${pct}%`
        if (progressPercentage) progressPercentage.textContent = `${pct}%`

        if (logConsole && status.logs) {
          const existingCount = logConsole.children.length
          if (status.logs.length > existingCount) {
            for (let i = existingCount; i < status.logs.length; i++) {
              const p = document.createElement('p')
              p.style.margin = '0'
              p.style.color = 'var(--text-secondary)'
              p.textContent = status.logs[i]
              logConsole.appendChild(p)
            }
            logConsole.scrollTop = logConsole.scrollHeight
          }
        }
      }

      if (!status.is_running) {
        sessionStorage.removeItem('activeSessionId')
        if (status.error) {
          stopPolling()
          if (progressMessage) {
            progressMessage.textContent = `Error: ${status.error}`
            progressMessage.style.color = 'var(--error)'
          }
          if (btnCancelProcess) btnCancelProcess.style.display = 'none'
          if (btnDismissProgress) btnDismissProgress.style.display = 'inline-flex'
        } else if (status.progress >= 1.0) {
          stopPolling()
          if (progressStepTitle) progressStepTitle.textContent = 'Complete!'
          showToast(
            status.kind === 'jira_push'
              ? status.message || 'Jira push complete'
              : 'Extraction Complete!',
            'success'
          )
          if (btnCancelProcess) btnCancelProcess.style.display = 'none'
          if (btnDismissProgress) btnDismissProgress.style.display = 'inline-flex'
          if (onComplete) await onComplete()
        } else {
          // System is idle (not running, no progress yet).
          stopPolling()
        }
      }
    } catch (e) {
      const next = ($pollStatus.get().pollErrorCount || 0) + 1
      setPollStatus({ pollErrorCount: next })
      console.error('Polling error', e)
      if (next >= 3 && progressMessage) {
        progressMessage.textContent = 'Reconnecting…'
        progressMessage.style.color = 'var(--warning)'
      }
    }
  }, 1000)
}
