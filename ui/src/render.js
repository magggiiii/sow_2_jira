// ui/src/render.js — UI.2b (ui-6)
//
// View layer: everything that writes to the DOM. Reads task state from the
// store ($taskData) and the filter checkboxes; renders the grouped task cards,
// the sidebar stats, toasts, and the progress overlay chrome. Pure-logic that
// this module leans on (stats, filtering, labels, status display) lives in
// api.js so it stays unit-testable.
import { $taskData } from './state.js'
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
  const template = getEl('taskCardTemplate')
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
    groupHeader.innerHTML = `
        <div class="task-group-title">
            <span class="task-group-icon">${hierarchy === 'flat' ? '📋' : '📦'}</span>
            <span class="task-group-label">${containerLabel}:</span>
            <span>${section}</span>
        </div>
        <div class="task-group-meta">
            <span class="task-group-count">${tasks.length} ${childLabel}${tasks.length !== 1 ? 's' : ''}</span>
            <span class="task-group-approved">${approvedCount}/${tasks.length} approved</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="group-chevron">
                <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
        </div>
    `

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
      if (!template) return
      const clone = template.content.cloneNode(true)
      const card = clone.querySelector('.task-card')

      card.querySelector('.task-title-text').textContent = t.title
      const ind = card.querySelector('.status-indicator')
      ind.classList.add(t.status.toLowerCase())

      const badgesEl = card.querySelector('.task-badges')
      ;(t.flags || []).forEach((f) => {
        const b = document.createElement('span')
        b.className = 'badge'
        b.textContent = typeof f === 'object' ? f.value : f
        badgesEl.appendChild(b)
      })

      const header = card.querySelector('.task-header')
      header.addEventListener('click', (e) => {
        if (e.target.tagName !== 'BUTTON' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
          card.classList.toggle('expanded')
          const svg = card.querySelector('.chevron')
          svg.style.transform = card.classList.contains('expanded') ? 'rotate(180deg)' : ''
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

      groupBody.appendChild(card)
    })

    taskListEl.appendChild(groupEl)
  })
}
