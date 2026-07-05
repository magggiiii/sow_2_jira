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

// --- ui-15: ONE status→bucket map — the single source of truth for BOTH the
// filter (shouldShowTask) AND the sidebar stats (computeStats). Previously the
// two disagreed: stats counted PUSHED in its own bucket while the filter leaked
// PUSHED into approved/pending, and "Pending" mapped to CLOSED in one place and
// something else in another. Now every TaskStatus resolves to exactly one of the
// four review buckets, verified against models/schemas.py :: TaskStatus.
//
//   pending  — OPEN, CLOSED, MERGED  (still under review / not human-actioned)
//   approved — APPROVED
//   rejected — REJECTED
//   pushed   — PUSHED  (has its own filter now, ui-15)
export const STATUS_BUCKET = {
  [TASK_STATUS.OPEN]: 'pending',
  [TASK_STATUS.CLOSED]: 'pending',
  [TASK_STATUS.MERGED]: 'pending',
  [TASK_STATUS.APPROVED]: 'approved',
  [TASK_STATUS.REJECTED]: 'rejected',
  [TASK_STATUS.PUSHED]: 'pushed',
}

// The bucket a task falls in. Unknown/blank statuses fall back to `pending` so a
// stray value is still visible + counted rather than silently dropped.
export function bucketOf(status) {
  return STATUS_BUCKET[String(status || '').toUpperCase()] || 'pending'
}

// --- Pure view-model helpers ----------------------------------------------

// Sidebar overview counts. Driven by the same STATUS_BUCKET map as the filter,
// so a status can never be counted in one bucket but filtered as another.
export function computeStats(tasks) {
  const list = tasks || []
  const counts = { total: list.length, pending: 0, approved: 0, rejected: 0, pushed: 0 }
  list.forEach((t) => {
    counts[bucketOf(t.status)] += 1
  })
  return counts
}

// Filter predicate. `filters` = { pending, approved, rejected, pushed, flagged }
// booleans — one flag per review bucket, driven by the SAME STATUS_BUCKET map as
// computeStats so filter + stats can never disagree. `flagged` is an extra
// AND-constraint (flagged-only) layered on top of the bucket toggles.
export function shouldShowTask(t, filters) {
  const f = filters || {}
  const bucket = bucketOf(t.status)
  if (f[bucket] === false) return false
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

// --- ui-16: Push pre-flight predicate --------------------------------------
//
// Pure so it can drive both the disabled state AND the tooltip. Push is only
// valid when a session is active, at least one task is approved, and Jira creds
// are saved. Returns { enabled, reason } — `reason` is the tooltip explaining a
// disabled button (empty when enabled). This absorbs the backend's synchronous
// `{success:false,"No approved tasks"}` return so it never round-trips.
export function pushPreflight({ activeSessionId, approvedCount, jiraConfigured } = {}) {
  if (!activeSessionId) return { enabled: false, reason: 'Select or run a session first' }
  if (!approvedCount || approvedCount <= 0) {
    return { enabled: false, reason: 'Approve at least one task before pushing' }
  }
  if (!jiraConfigured) {
    return { enabled: false, reason: 'Add Jira server + API token in Settings first' }
  }
  return { enabled: true, reason: '' }
}

// --- ui-18: session-switch running-state decision --------------------------
//
// Pure: given the /api/status payload for the session being switched to, decide
// whether to reopen the progress overlay + resume polling ('resume') or just
// load + render its tasks ('load'). Prevents rendering a stale empty list over a
// run that's still in flight.
export function decideSessionSwitch(status) {
  return status && status.is_running ? 'resume' : 'load'
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
