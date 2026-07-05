// ui/src/render.js — UI.2b (ui-6)
//
// View layer: everything that writes to the DOM. Reads task state from the
// store ($taskData) and the filter checkboxes; renders the grouped task cards,
// the sidebar stats, toasts, and the progress overlay chrome. Pure-logic that
// this module leans on (stats, filtering, labels, status display) lives in
// api.js so it stays unit-testable.
import { $taskData, setTaskData } from './state.js'
import {
  TASK_STATUS,
  computeStats,
  shouldShowTask,
  statusDisplay,
  isApproved,
  isRejected,
  containerLabelFor,
  childLabelFor,
} from './api.js'

// Safe element lookup (kept from the original getEl).
export const getEl = (id) => {
  const el = document.getElementById(id)
  if (!el) console.warn(`Element with ID "${id}" not found.`)
  return el
}

// --- Security: escape user/LLM-derived text before it touches innerHTML ------
//
// ui-13: section_title (and any other free text) is stored-XSS-prone. Prefer
// textContent where possible; this helper is the fallback for the few spots
// that still build markup as strings.
export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// Stable, collision-safe DOM ids derived from a task id (arbitrary user data).
const taskCardId = (taskId) => `task-card-${encodeURIComponent(String(taskId))}`
const taskBodyId = (taskId) => `task-body-${encodeURIComponent(String(taskId))}`

// A collapsed header must carry a human-readable status word beside the dot —
// WCAG 1.4.1, status is never conveyed by colour alone. Falls back to the raw
// status so an unmapped value is still labelled rather than blank.
function statusLabelText(status) {
  const disp = statusDisplay(status)
  if (disp) return disp.text
  const s = String(status || '').toUpperCase()
  if (s === TASK_STATUS.OPEN || s === TASK_STATUS.CLOSED) return 'Pending'
  if (s === TASK_STATUS.MERGED) return 'Merged'
  return s ? s.charAt(0) + s.slice(1).toLowerCase() : 'Pending'
}

// Suppresses the "No SOW Loaded" flash before the first load resolves.
let hasLoadedTasks = false
export function markTasksLoaded() {
  hasLoadedTasks = true
}

// --- Toast -----------------------------------------------------------------

export function showToast(message, type = 'info') {
  const toastContainer = getEl('toastContainer')
  if (!toastContainer) return
  const toast = document.createElement('div')
  toast.className = 'toast'
  toast.textContent = message
  if (type === 'error') toast.style.borderLeftColor = 'var(--error)'
  if (type === 'success') toast.style.borderLeftColor = 'var(--success)'

  toastContainer.appendChild(toast)
  const dismiss = () => {
    toast.style.opacity = '0'
    setTimeout(() => toast.remove(), 300)
  }
  if (type === 'error') {
    // Errors persist until dismissed so the user can act before they vanish.
    toast.style.cursor = 'pointer'
    toast.title = 'Click to dismiss'
    toast.addEventListener('click', dismiss)
  } else {
    setTimeout(dismiss, 3000)
  }
}

// --- Stats -----------------------------------------------------------------

export function updateStats() {
  const stats = computeStats($taskData.get().tasks)
  if (getEl('statsTotal')) getEl('statsTotal').textContent = stats.total
  if (getEl('statsApproved')) getEl('statsApproved').textContent = stats.approved
  if (getEl('statsRejected')) getEl('statsRejected').textContent = stats.rejected
  if (getEl('statsPending')) getEl('statsPending').textContent = stats.pending
  if (getEl('statsPushed')) getEl('statsPushed').textContent = stats.pushed
}

// --- Task list -------------------------------------------------------------

function currentFilters() {
  const fPending = getEl('filterPending')
  const fApproved = getEl('filterApproved')
  const fRejected = getEl('filterRejected')
  const fFlagged = getEl('filterFlagged')
  return {
    pending: fPending ? fPending.checked : true,
    approved: fApproved ? fApproved.checked : true,
    rejected: fRejected ? fRejected.checked : false,
    flagged: fFlagged ? fFlagged.checked : false,
  }
}

// `onSave(updatedTask)` is injected so render.js has no direct dependency on the
// api/save flow — the entry module wires it.
export function renderTasks(onSave) {
  const taskListEl = getEl('taskList')
  if (!taskListEl) return
  taskListEl.innerHTML = ''

  const taskData = $taskData.get()
  const filters = currentFilters()
  const visibleTasks = (taskData.tasks || []).filter((t) => shouldShowTask(t, filters))

  if (visibleTasks.length === 0 && hasLoadedTasks) {
    const emptyEl = document.createElement('div')
    emptyEl.className = 'empty-state'
    emptyEl.innerHTML =
      '<h2>No Statement of Work Loaded</h2><p>Upload a SOW PDF to extract Jira tasks.</p>'
    taskListEl.appendChild(emptyEl)
  }

  if (getEl('showingCount')) {
    getEl('showingCount').textContent =
      `Showing ${visibleTasks.length} of ${taskData.tasks.length} tasks`
  }

  // Group tasks by SOW section.
  const groups = {}
  visibleTasks.forEach((t) => {
    const section =
      t.source_refs && t.source_refs.length > 0 ? t.source_refs[0].section_title : 'General'
    if (!groups[section]) groups[section] = []
    groups[section].push(t)
  })

  const hierarchy = getEl('jiraHierarchy')?.value || 'epic_task'
  const containerLabel = containerLabelFor(hierarchy)
  const childLabel = childLabelFor(hierarchy)

  Object.entries(groups).forEach(([section, tasks]) => {
    const groupEl = document.createElement('div')
    groupEl.className = 'task-group'

    const approvedCount = tasks.filter((t) => isApproved(t)).length
    const groupHeader = document.createElement('div')
    groupHeader.className = 'task-group-header'
    // Static chrome via innerHTML (no user data), then the section title is set
    // via textContent — ui-13: never interpolate section_title into markup.
    groupHeader.innerHTML = `
        <div class="task-group-title">
            <span class="task-group-icon">${hierarchy === 'flat' ? '📋' : '📦'}</span>
            <span class="task-group-label">${escapeHtml(containerLabel)}:</span>
            <span class="task-group-section"></span>
        </div>
        <div class="task-group-meta">
            <span class="task-group-count">${tasks.length} ${escapeHtml(childLabel)}${tasks.length !== 1 ? 's' : ''}</span>
            <span class="task-group-approved">${approvedCount}/${tasks.length} approved</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="group-chevron">
                <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
        </div>
    `
    groupHeader.querySelector('.task-group-section').textContent = section

    const groupBody = document.createElement('div')
    groupBody.className = 'task-group-body'

    groupHeader.addEventListener('click', () => {
      groupEl.classList.toggle('collapsed')
      const svg = groupHeader.querySelector('.group-chevron')
      svg.style.transform = groupEl.classList.contains('collapsed') ? 'rotate(-90deg)' : ''
    })

    groupEl.appendChild(groupHeader)
    groupEl.appendChild(groupBody)

    tasks.forEach((t) => {
      const card = buildTaskCard(t, onSave)
      if (card) groupBody.appendChild(card)
    })

    taskListEl.appendChild(groupEl)
  })
}

// Builds ONE task card DOM node from a task + the save callback. Shared by the
// full render (renderTasks) and the in-place patch (patchTaskCard) so the two
// paths can never drift. Returns null when the <template> is absent.
export function buildTaskCard(t, onSave) {
  const template = getEl('taskCardTemplate')
  if (!template) return null

  const clone = template.content.cloneNode(true)
  const card = clone.querySelector('.task-card')

  // Stable id so a single card can be located + swapped in place (ui-11).
  card.id = taskCardId(t.id)
  card.dataset.taskId = String(t.id)

  card.querySelector('.task-title-text').textContent = t.title
  const ind = card.querySelector('.status-indicator')
  ind.classList.add(String(t.status || '').toLowerCase())

  // ui-12: status is never colour-only — a text word sits beside the dot on the
  // (collapsed) header. Inserted right after the status dot.
  const titleArea = card.querySelector('.task-title-area')
  if (titleArea && ind) {
    const statusLabel = document.createElement('span')
    statusLabel.className = 'status-label'
    statusLabel.textContent = statusLabelText(t.status)
    ind.insertAdjacentElement('afterend', statusLabel)
  }

  const badgesEl = card.querySelector('.task-badges')
  ;(t.flags || []).forEach((f) => {
    const b = document.createElement('span')
    b.className = 'badge'
    b.textContent = typeof f === 'object' ? f.value : f
    badgesEl.appendChild(b)
  })

  // ui-12: the header is an accessible disclosure button.
  const header = card.querySelector('.task-header')
  const body = card.querySelector('.task-body')
  if (body) body.id = taskBodyId(t.id)
  header.setAttribute('role', 'button')
  header.setAttribute('tabindex', '0')
  header.setAttribute('aria-expanded', 'false')
  if (body) header.setAttribute('aria-controls', taskBodyId(t.id))

  const toggle = () => {
    const expanded = card.classList.toggle('expanded')
    header.setAttribute('aria-expanded', expanded ? 'true' : 'false')
    const svg = card.querySelector('.chevron')
    if (svg) svg.style.transform = expanded ? 'rotate(180deg)' : ''
  }
  const isControl = (el) =>
    el.tagName === 'BUTTON' || el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'

  header.addEventListener('click', (e) => {
    if (!isControl(e.target)) toggle()
  })
  // Enter/Space mirror the click (native <button> semantics on a non-button).
  header.addEventListener('keydown', (e) => {
    if (isControl(e.target)) return
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault()
      toggle()
    }
  })

  if (t.source_refs && t.source_refs.length > 0) {
    const ref = t.source_refs[0]
    const conf = (t.confidence * 100).toFixed(0)
    card.querySelector('.source-ref').textContent =
      `📄 Pages ${ref.page_start}-${ref.page_end} | Confidence: ${conf}%`
  }

  card.querySelector('.task-edit-title').value = t.title || ''
  card.querySelector('.task-edit-desc').value = t.short_description || ''
  card.querySelector('.task-edit-usecase').value = t.use_case || ''
  card.querySelector('.task-edit-ac').value = (t.acceptance_criteria || []).join('\n')
  card.querySelector('.task-edit-cc').value = (t.considerations_constraints || []).join('\n')
  card.querySelector('.task-edit-del').value = (t.deliverables || []).join('\n')

  const stText = card.querySelector('.task-status-text')
  const disp = statusDisplay(t.status)
  if (disp) {
    stText.textContent = disp.text
    stText.style.color = disp.color
  }

  const getUpdatedData = () => ({
    id: t.id,
    title: card.querySelector('.task-edit-title').value,
    short_description: card.querySelector('.task-edit-desc').value,
    use_case: card.querySelector('.task-edit-usecase').value,
    acceptance_criteria: card
      .querySelector('.task-edit-ac')
      .value.split('\n')
      .filter((x) => x.trim() !== ''),
    considerations_constraints: card
      .querySelector('.task-edit-cc')
      .value.split('\n')
      .filter((x) => x.trim() !== ''),
    deliverables: card
      .querySelector('.task-edit-del')
      .value.split('\n')
      .filter((x) => x.trim() !== ''),
    status: t.status,
  })

  card.querySelector('.btn-save').addEventListener('click', (e) => {
    e.stopPropagation()
    onSave(getUpdatedData())
  })

  const btnApprove = card.querySelector('.btn-approve')
  const btnReject = card.querySelector('.btn-reject')

  if (isApproved(t)) {
    btnApprove.style.display = 'none'
  } else {
    btnApprove.addEventListener('click', (e) => {
      e.stopPropagation()
      const dt = getUpdatedData()
      dt.status = TASK_STATUS.APPROVED
      onSave(dt)
    })
  }

  if (isRejected(t)) {
    btnReject.style.display = 'none'
  } else {
    btnReject.addEventListener('click', (e) => {
      e.stopPropagation()
      const dt = getUpdatedData()
      dt.status = TASK_STATUS.REJECTED
      onSave(dt)
    })
  }

  return card
}

// --- Optimistic in-place patch (ui-11) -------------------------------------
//
// Replaces the old "wipe #taskList + full refetch on every save/approve/reject"
// so a card's neighbours keep their expanded/collapsed state across an edit.
//
// Steps:
//   1. Merge the updated fields into the store copy of the task (so filters and
//      stats stay consistent, and a later full render is correct).
//   2. Swap ONLY the changed card's DOM node in place, preserving whether it was
//      expanded. Every other card node is left untouched.
//
// If the patched task now fails the active filter, it is removed rather than
// re-rendered. If the card isn't currently in the DOM (e.g. was filtered out),
// a full render is triggered so it can appear/disappear correctly.
export function patchTaskCard(updatedTask, onSave) {
  if (!updatedTask || updatedTask.id == null) return

  // 1. Merge into the store so downstream reads (stats/filter/full re-render)
  //    reflect the edit.
  const data = $taskData.get()
  const tasks = (data.tasks || []).map((t) =>
    String(t.id) === String(updatedTask.id) ? { ...t, ...updatedTask } : t
  )
  setTaskData({ ...data, tasks })
  const merged = tasks.find((t) => String(t.id) === String(updatedTask.id))
  if (!merged) return

  // 2. In-place DOM swap of just this card.
  const existing = document.getElementById(taskCardId(merged.id))
  if (!existing) {
    // Card isn't on screen (filtered out / not yet rendered) — fall back to a
    // full render so it can appear in the right group. This is NOT the hot path.
    renderTasks(onSave)
    updateStats()
    return
  }

  const filters = currentFilters()
  if (!shouldShowTask(merged, filters)) {
    // Now hidden by the active filter — remove it (and prune an emptied group).
    const group = existing.closest('.task-group')
    existing.remove()
    if (group && group.querySelectorAll('.task-card').length === 0) group.remove()
    updateStats()
    return
  }

  const wasExpanded = existing.classList.contains('expanded')
  const fresh = buildTaskCard(merged, onSave)
  if (!fresh) return
  if (wasExpanded) {
    fresh.classList.add('expanded')
    const header = fresh.querySelector('.task-header')
    if (header) header.setAttribute('aria-expanded', 'true')
    const svg = fresh.querySelector('.chevron')
    if (svg) svg.style.transform = 'rotate(180deg)'
  }
  existing.replaceWith(fresh)
  updateStats()
}
