// ui/src/api.js — UI.2b (ui-8)
//
// API adapter + view-model. ONE home for:
//   1. the TaskStatus enum strings (were inlined ~4x across the old app.js:
//      updateStats, shouldShow, render status-text, approve/reject handlers).
//   2. pure view-model helpers derived from those statuses (stats, filter,
//      display label/colour, hierarchy labels) — trivially unit-testable.
//   3. thin fetch wrappers so a backend field rename is a one-file edit.
//
// A backend TaskStatus rename now touches exactly TASK_STATUS below, not five
// scattered string literals.

// Mirror of models/schemas.py :: TaskStatus. Single source of truth.
export const TASK_STATUS = {
  OPEN: 'OPEN',
  CLOSED: 'CLOSED',
  MERGED: 'MERGED',
  REJECTED: 'REJECTED',
  APPROVED: 'APPROVED',
  PUSHED: 'PUSHED',
}

// --- Pure view-model helpers ----------------------------------------------

// Sidebar overview counts. `pending` is everything not in a terminal bucket.
export function computeStats(tasks) {
  const list = tasks || []
  const total = list.length
  const approved = list.filter((t) => t.status === TASK_STATUS.APPROVED).length
  const rejected = list.filter((t) => t.status === TASK_STATUS.REJECTED).length
  const pushed = list.filter((t) => t.status === TASK_STATUS.PUSHED).length
  const pending = total - approved - rejected - pushed
  return { total, approved, rejected, pushed, pending }
}

// Filter predicate. `filters` = { pending, approved, rejected, flagged } booleans.
// Preserves the exact behaviour of the old `shouldShow` (CLOSED == pending
// bucket; PUSHED hidden only when both approved AND pending are off).
export function shouldShowTask(t, filters) {
  const f = filters || {}
  if (t.status === TASK_STATUS.CLOSED && !f.pending) return false
  if (t.status === TASK_STATUS.APPROVED && !f.approved) return false
  if (t.status === TASK_STATUS.REJECTED && !f.rejected) return false
  if (t.status === TASK_STATUS.PUSHED && !f.approved && !f.pending) return false
  if (f.flagged && (!t.flags || t.flags.length === 0)) return false
  return true
}

// Inline per-card status label + colour token. Returns null for statuses that
// showed no inline text in the original (OPEN / CLOSED / MERGED).
export function statusDisplay(status) {
  switch (status) {
    case TASK_STATUS.APPROVED:
      return { text: '✅ Approved', color: 'var(--success)' }
    case TASK_STATUS.REJECTED:
      return { text: '❌ Rejected', color: 'var(--error)' }
    case TASK_STATUS.PUSHED:
      return { text: '🚀 Pushed', color: 'var(--accent-primary)' }
    default:
      return null
  }
}

// A task is "already actioned" — its Approve/Reject buttons are hidden.
export function isApproved(t) {
  return t.status === TASK_STATUS.APPROVED || t.status === TASK_STATUS.PUSHED
}
export function isRejected(t) {
  return t.status === TASK_STATUS.REJECTED || t.status === TASK_STATUS.PUSHED
}

// Group / child labels derived from the selected Jira hierarchy.
export function containerLabelFor(hierarchy) {
  if (hierarchy === 'story_subtask') return 'Story'
  if (hierarchy === 'epic_task') return 'Epic'
  return 'Section'
}
export function childLabelFor(hierarchy) {
  return hierarchy === 'story_subtask' ? 'Sub-task' : 'Task'
}

// --- Fetch adapters --------------------------------------------------------
//
// Centralized so a route/field rename is one edit. `sessionQuery` mirrors the
// old getSessionQuery(): "?session_id=..." or "".

export function sessionQuery(sessionId) {
  return sessionId ? `?session_id=${sessionId}` : ''
}

export async function fetchProviders() {
  const res = await fetch('/api/providers')
  const data = await res.json()
  return data.providers || {}
}

export async function fetchSettings() {
  const res = await fetch('/api/settings')
  return res.json()
}

export async function saveSettings(payload) {
  return fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function fetchModels(provider, payload) {
  const res = await fetch(`/api/providers/${provider}/models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.detail || 'Model discovery failed')
  return data.models || []
}

export async function fetchSessions() {
  const res = await fetch('/api/sessions')
  return res.json()
}

export async function fetchTasks(sessionId) {
  const res = await fetch('/api/tasks' + sessionQuery(sessionId))
  return res.json()
}

export async function fetchStatus(sessionId) {
  const res = await fetch('/api/status' + sessionQuery(sessionId))
  return res.json()
}

export async function postTask(sessionId, task) {
  return fetch('/api/tasks' + sessionQuery(sessionId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(task),
  })
}

export async function approveAll(sessionId) {
  const res = await fetch('/api/tasks/approve_all' + sessionQuery(sessionId), { method: 'POST' })
  return res.json()
}

export async function uploadFile(file) {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch('/api/upload', { method: 'POST', body: formData })
  return res.json()
}

export async function startProcess(req) {
  return fetch('/api/process', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
}

export async function pushToJira(sessionId, req) {
  const res = await fetch('/api/push' + sessionQuery(sessionId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  return res.json()
}

export async function cancelRun(sessionId, signal) {
  return fetch(`/api/cancel/${sessionId}`, { method: 'POST', signal })
}

export async function deleteSession(sessionId) {
  return fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' })
}
