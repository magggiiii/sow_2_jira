// ui/src/state.js — UI.2b (ui-7)
//
// The three previously hand-synced globals from the old IIFE, now a tiny
// reactive store built on nanostores (~1KB, framework-agnostic, no JSX):
//
//   activeSessionId  (was `let activeSessionId` + manual sessionStorage sync)
//   taskData         (was `let taskData = { tasks, config }`)
//   pollStatus       (was `let statusInterval` + `let pollErrorCount` locals)
//
// Screens read from these atoms instead of reaching into module-level `let`s,
// so a status change fans out to every subscriber automatically.
import { atom } from 'nanostores'

const SESSION_KEY = 'activeSessionId'

// --- Atoms -----------------------------------------------------------------

// The run currently displayed / being polled. `null` == "new extraction" screen.
export const $activeSessionId = atom(
  typeof sessionStorage !== 'undefined' ? sessionStorage.getItem(SESSION_KEY) : null
)

// The task payload returned by /api/tasks: { tasks: [...], config: {...}, ... }.
export const $taskData = atom({ tasks: [], config: {} })

// Poll lifecycle state (the old `statusInterval` + `pollErrorCount`).
export const $pollStatus = atom({ isRunning: false, pollErrorCount: 0 })

// --- Actions ---------------------------------------------------------------

export function setActiveSession(id) {
  $activeSessionId.set(id)
  if (typeof sessionStorage !== 'undefined') {
    if (id) sessionStorage.setItem(SESSION_KEY, id)
    else sessionStorage.removeItem(SESSION_KEY)
  }
}

export function clearActiveSession() {
  setActiveSession(null)
}

export function setTaskData(payload) {
  $taskData.set(payload || { tasks: [], config: {} })
}

export function resetTaskData() {
  $taskData.set({ tasks: [], config: {} })
}

export function setPollStatus(patch) {
  $pollStatus.set({ ...$pollStatus.get(), ...patch })
}
