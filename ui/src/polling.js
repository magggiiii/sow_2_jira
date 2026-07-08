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

// --- error_class → recovery mapping (UI.3, ui-9) ---------------------------
//
// Mirror of core/errors.py :: ErrorClass. The backend surfaces the failure
// severity on ProcessingStatus.error_class; push failures come through this
// SAME polling branch (the /api/push POST never returns error_class
// synchronously — see server.py). Each class drives a distinct recovery so the
// old dead-end "Dismiss"-for-everything state is gone.
export const ERROR_CLASS = {
  TRANSIENT: 'TRANSIENT',
  USER_FIXABLE: 'USER_FIXABLE',
  TERMINAL: 'TERMINAL',
}

// Pure, unit-testable. Maps an error_class to its severity chip (a TEXT label,
// never colour-only — WCAG 1.4.1) and its primary recovery action. Unknown /
// missing values fall back to a neutral Dismiss so we never hard-fail the UX.
export function errorRecovery(errorClass) {
  const key = (errorClass || '').toString().toUpperCase()
  switch (key) {
    case ERROR_CLASS.TRANSIENT:
      return {
        chipLabel: 'Retryable',
        chipClass: 'sev-transient',
        action: 'retry',
        actionLabel: 'Retry',
      }
    case ERROR_CLASS.USER_FIXABLE:
      return {
        chipLabel: 'Check credentials',
        chipClass: 'sev-fixable',
        action: 'open_settings',
        actionLabel: 'Open Settings',
      }
    case ERROR_CLASS.TERMINAL:
    default:
      return {
        chipLabel: 'Failed',
        chipClass: 'sev-terminal',
        action: 'dismiss',
        actionLabel: 'Dismiss',
      }
  }
}

// Renders the severity chip (built in JS — no dependency on new index.html
// markup) immediately ABOVE the failure message, and wires the primary action
// button to the matching callback. `els` = { message, primaryBtn }, `handlers`
// = { onRetry, onOpenSettings, onDismiss } injected by main.js. Idempotent: a
// re-render replaces any existing chip rather than stacking them.
export function renderFailureRecovery(errorClass, els, handlers) {
  const { message, primaryBtn } = els || {}
  const { onRetry, onOpenSettings, onDismiss } = handlers || {}
  const rec = errorRecovery(errorClass)

  if (message && message.parentNode) {
    const parent = message.parentNode
    const existing = parent.querySelector('.sev-chip')
    if (existing) existing.remove()

    const chip = document.createElement('span')
    chip.className = `sev-chip ${rec.chipClass}`
    chip.textContent = rec.chipLabel // text label — not colour alone.
    parent.insertBefore(chip, message)
  }

  if (primaryBtn) {
    primaryBtn.textContent = rec.actionLabel
    primaryBtn.style.display = 'inline-flex'
    // Mutate in place (works whether or not the button is attached) and keep a
    // single listener: stash the current action on the element and dispatch
    // through it so re-rendering with a new class never stacks handlers.
    primaryBtn._recoveryAction = rec.action
    if (!primaryBtn._recoveryBound) {
      primaryBtn._recoveryBound = true
      primaryBtn.addEventListener('click', () => {
        // Only act while a recovery is ACTIVE. After a later successful run,
        // clearFailureRecovery() nulls the action; the button reverts to a plain
        // Dismiss owned by main.js (which just hides the overlay). Without this
        // guard the stale listener would fire onDismiss → resetToNewSession and
        // silently wipe the freshly-loaded tasks.
        const action = primaryBtn._recoveryAction
        if (!action) return
        if (action === 'retry' && onRetry) onRetry()
        else if (action === 'open_settings' && onOpenSettings) onOpenSettings()
        else if (action === 'dismiss' && onDismiss) onDismiss()
      })
    }
    return primaryBtn
  }
  return null
}

// Tears down any failure chrome from a prior run so a fresh success/idle state
// on a re-used overlay never inherits a stale "Retry" label or severity chip.
export function clearFailureRecovery(els) {
  const { message, primaryBtn } = els || {}
  if (message && message.parentNode) {
    const chip = message.parentNode.querySelector('.sev-chip')
    if (chip) chip.remove()
  }
  if (primaryBtn) {
    // Null (not 'dismiss') so the stale recovery listener no-ops on a re-used
    // overlay after a success — leaving the plain Dismiss (main.js) to just hide
    // the overlay without resetting/wiping the loaded tasks.
    primaryBtn._recoveryAction = null
    primaryBtn.textContent = 'Dismiss'
  }
}

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

// `onComplete` is the data-load flow injected by main.js. `recovery` bundles the
// error_class-aware failure callbacks — { onRetry, onOpenSettings, onDismiss } —
// also injected by main.js (polling.js can't call the main flow directly without
// an import cycle). All are optional; the failure branch degrades gracefully.
export function startStatusPolling(onComplete, recovery = {}) {
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
          // error_class-aware failure UX (UI.3): render the severity chip above
          // the message and re-purpose the dismiss button as the class-specific
          // primary recovery (Retry / Open Settings / Dismiss).
          renderFailureRecovery(
            status.error_class,
            { message: progressMessage, primaryBtn: btnDismissProgress },
            recovery
          )
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
          clearFailureRecovery({ message: progressMessage, primaryBtn: btnDismissProgress })
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
