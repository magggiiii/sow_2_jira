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

// --- ui-14: keyboard triage dispatch ---------------------------------------
//
// Pure + unit-testable. Maps a keydown into a triage action, but ONLY when the
// user is NOT typing into a form control (input/textarea/select/contentEditable)
// — otherwise `a` in a title field would reject the card. Returns one of
// 'approve' | 'reject' | 'expand' | 'next' | 'prev' | null.
//
// `ctx` = { key, isTyping }. Arrow keys mirror j/k so both muscle memories work.
export function resolveTriageAction({ key, isTyping } = {}) {
  if (isTyping) return null
  switch (key) {
    case 'a':
    case 'A':
      return 'approve'
    case 'r':
    case 'R':
      return 'reject'
    case 'e':
    case 'E':
      return 'expand'
    case 'j':
    case 'J':
    case 'ArrowDown':
      return 'next'
    case 'k':
    case 'K':
    case 'ArrowUp':
      return 'prev'
    default:
      return null
  }
}

// True when focus is in a text-entry control, so triage keys must NOT fire.
export function isTypingTarget(el) {
  if (!el) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (el.isContentEditable) return true
  return false
}

// --- ui-14: confidence sort ------------------------------------------------
//
// Returns a NEW array (does not mutate). `direction` 'asc' = lowest-confidence
// first (the useful triage default — surface the risky ones). 'none' preserves
// the original order. Missing confidence sorts as 0 (treated as most-uncertain).
export function sortTasksByConfidence(tasks, direction = 'none') {
  const list = [...(tasks || [])]
  if (direction === 'none') return list
  const conf = (t) => (typeof t.confidence === 'number' ? t.confidence : 0)
  list.sort((a, b) => (direction === 'asc' ? conf(a) - conf(b) : conf(b) - conf(a)))
  return list
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

// --- ui-22: first-run de-emphasis ------------------------------------------
//
// On a genuine first run (no sessions, no tasks) the Filters and Global Actions
// blocks are noise — there's nothing to filter or act on. Toggle a body class so
// the stylesheet can dim/disable them and steer the eye to Upload. Idempotent.
export function applyFirstRunState(isFirstRun) {
  if (typeof document === 'undefined' || !document.body) return
  document.body.classList.toggle('is-first-run', !!isFirstRun)
}

// --- ui-23: push-completion results panel ----------------------------------
//
// After a push completes and the task data reloads, show a real results panel
// instead of a bare toast: every PUSHED task links to its Jira issue
// (`jira_server`/browse/KEY), and any failure is surfaced with a severity chip.
//
// Backend reality (server.py :: run_push_task): per-task success is persisted by
// flipping status→PUSHED + jira_issue_key on disk, but per-task FAILURE detail
// is NOT — only an aggregate first_error / first_error_class rides the polling
// status. So the panel renders PUSHED tasks as clickable links, and shows the
// aggregate failure (count + first error chip) when the push wasn't fully clean.
//
// Renders into #pushResultsPanel (created lazily above the task list). `opts` =
// { tasks, jiraServerUrl, failedCount, firstError, errorChip } — errorChip is
// { label, className } from errorRecovery (so the WCAG text-label chip matches
// the failure UX). Returns the panel element (or null if there's nothing to show).
function jiraBrowseUrl(server, key) {
  if (!server || !key) return null
  const s = String(server).trim()
  // Only http(s): reject javascript:/data:/etc so a malicious or fat-fingered
  // Jira-server SETTING (user-supplied, unvalidated server-side) can't inject an
  // executable href into the results link — matters for the multi-user pivot
  // where one user's setting is clicked by another. Falls back to plain text.
  if (!/^https?:\/\//i.test(s)) return null
  return `${s.replace(/\/+$/, '')}/browse/${encodeURIComponent(key)}`
}

export function renderPushResults(opts = {}) {
  const { tasks = [], jiraServerUrl = '', failedCount = 0, firstError = '', errorChip } = opts
  const pushed = (tasks || []).filter((t) => t.status === TASK_STATUS.PUSHED && t.jira_issue_key)

  const taskListEl = getEl('taskList')
  if (!taskListEl) return null

  // Idempotent: replace any prior panel.
  const prior = getEl('pushResultsPanel')
  if (prior) prior.remove()

  if (pushed.length === 0 && failedCount === 0) return null

  const panel = document.createElement('section')
  panel.id = 'pushResultsPanel'
  panel.className = 'push-results-panel'
  panel.setAttribute('role', 'status')
  panel.setAttribute('aria-live', 'polite')

  const head = document.createElement('div')
  head.className = 'push-results-head'
  const title = document.createElement('h3')
  title.textContent = 'Jira Push Results'
  const summary = document.createElement('span')
  summary.className = 'push-results-summary'
  summary.textContent = `${pushed.length} pushed${failedCount ? `, ${failedCount} failed` : ''}`
  const close = document.createElement('button')
  close.className = 'btn-icon push-results-close'
  close.setAttribute('aria-label', 'Dismiss push results')
  close.textContent = '×'
  close.addEventListener('click', () => panel.remove())
  head.append(title, summary, close)
  panel.appendChild(head)

  // Aggregate failure chip (per-task failure detail isn't persisted server-side).
  if (failedCount > 0) {
    const fail = document.createElement('div')
    fail.className = 'push-results-failure'
    if (errorChip && errorChip.label) {
      const chip = document.createElement('span')
      chip.className = `sev-chip ${errorChip.className || 'sev-terminal'}`
      chip.textContent = errorChip.label
      fail.appendChild(chip)
    }
    const msg = document.createElement('span')
    msg.className = 'text-error'
    msg.textContent = firstError
      ? `${failedCount} task(s) failed — ${firstError}`
      : `${failedCount} task(s) failed to push.`
    fail.appendChild(msg)
    panel.appendChild(fail)
  }

  if (pushed.length > 0) {
    const ul = document.createElement('ul')
    ul.className = 'push-results-list'
    pushed.forEach((t) => {
      const li = document.createElement('li')
      const name = document.createElement('span')
      name.className = 'push-results-title'
      name.textContent = t.title || t.jira_issue_key
      const url = jiraBrowseUrl(jiraServerUrl, t.jira_issue_key)
      if (url) {
        const a = document.createElement('a')
        a.className = 'push-results-key text-success'
        a.href = url
        a.target = '_blank'
        a.rel = 'noopener'
        a.textContent = t.jira_issue_key
        li.append(name, a)
      } else {
        const key = document.createElement('span')
        key.className = 'push-results-key text-success'
        key.textContent = t.jira_issue_key
        li.append(name, key)
      }
      ul.appendChild(li)
    })
    panel.appendChild(ul)
  }

  taskListEl.parentNode.insertBefore(panel, taskListEl)
  return panel
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
  const fPushed = getEl('filterPushed')
  const fFlagged = getEl('filterFlagged')
  return {
    pending: fPending ? fPending.checked : true,
    approved: fApproved ? fApproved.checked : true,
    rejected: fRejected ? fRejected.checked : false,
    // ui-15: dedicated Pushed filter. Default ON so pushed tasks stay visible
    // (they're the audit trail of what shipped) unless explicitly hidden.
    pushed: fPushed ? fPushed.checked : true,
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
  const allTasks = taskData.tasks || []
  const filters = currentFilters()
  const sortSel = getEl('sortConfidence')
  const sortDir = sortSel ? sortSel.value : 'none'
  const visibleTasks = sortTasksByConfidence(
    allTasks.filter((t) => shouldShowTask(t, filters)),
    sortDir
  )

  // ui-22: genuine first-run state — zero sessions AND zero tasks. A centered,
  // icon-led empty pane with a primary CTA that points at the upload zone. Only
  // shown once the first load has resolved (no flash) AND nothing exists.
  applyFirstRunState(allTasks.length === 0 && hasLoadedTasks)

  if (visibleTasks.length === 0 && hasLoadedTasks) {
    const emptyEl = document.createElement('div')
    emptyEl.className = 'empty-state'
    if (allTasks.length === 0) {
      // First-run: a real onboarding pane (icon + CTA), not a terse line.
      emptyEl.classList.add('empty-state-onboarding')
      const heading = document.createElement('h2')
      heading.textContent = 'No Statement of Work loaded yet'
      const sub = document.createElement('p')
      sub.textContent = 'Upload a SOW PDF and the pipeline will extract review-ready Jira tasks.'
      const cta = document.createElement('button')
      cta.className = 'btn btn-primary'
      cta.id = 'emptyUploadCta'
      cta.textContent = 'Upload SOW'
      cta.addEventListener('click', () => {
        const zone = getEl('uploadZone')
        if (zone) {
          zone.scrollIntoView({ behavior: 'smooth', block: 'center' })
          zone.classList.add('upload-zone-highlight')
          setTimeout(() => zone.classList.remove('upload-zone-highlight'), 1600)
          zone.click()
        }
      })
      const icon = document.createElement('div')
      icon.className = 'empty-state-icon'
      icon.setAttribute('aria-hidden', 'true')
      icon.innerHTML =
        '<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="12" y1="18" x2="12" y2="12"></line><polyline points="9 15 12 12 15 15"></polyline></svg>'
      emptyEl.append(icon, heading, sub, cta)
    } else {
      // Tasks exist but all are filtered out — a filter-scoped message.
      const heading = document.createElement('h2')
      heading.textContent = 'No tasks match the current filters'
      const sub = document.createElement('p')
      sub.textContent = 'Adjust the filters in the sidebar to see more tasks.'
      emptyEl.append(heading, sub)
    }
    taskListEl.appendChild(emptyEl)
  }

  if (getEl('showingCount')) {
    getEl('showingCount').textContent =
      `Showing ${visibleTasks.length} of ${allTasks.length} tasks`
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

  // ui-14: confidence badge on the collapsed header (before the flag badges so
  // the risk signal reads first). Text-labelled ("Conf 62%") + a severity class
  // by band — never colour alone (WCAG 1.4.1).
  const badgesEl = card.querySelector('.task-badges')
  if (typeof t.confidence === 'number') {
    const pct = Math.round(t.confidence * 100)
    const conf = document.createElement('span')
    const band = pct < 60 ? 'conf-low' : pct < 80 ? 'conf-mid' : 'conf-high'
    conf.className = `badge conf-badge ${band}`
    conf.textContent = `Conf ${pct}%`
    conf.title = 'Extraction confidence'
    badgesEl.appendChild(conf)
  }
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
      `Pages ${ref.page_start}-${ref.page_end} · Confidence: ${conf}%`
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

  const approve = () => {
    const dt = getUpdatedData()
    dt.status = TASK_STATUS.APPROVED
    onSave(dt)
  }
  const reject = () => {
    const dt = getUpdatedData()
    dt.status = TASK_STATUS.REJECTED
    onSave(dt)
  }

  if (isApproved(t)) {
    btnApprove.style.display = 'none'
  } else {
    btnApprove.addEventListener('click', (e) => {
      e.stopPropagation()
      approve()
    })
  }

  if (isRejected(t)) {
    btnReject.style.display = 'none'
  } else {
    btnReject.addEventListener('click', (e) => {
      e.stopPropagation()
      reject()
    })
  }

  // ui-14: the card is keyboard-focusable and exposes its triage verbs so the
  // document-level triage handler (main.js) can act on the FOCUSED card without
  // reaching into card internals. Approve/reject no-op when already actioned.
  card.setAttribute('tabindex', '0')
  card._triage = {
    approve: isApproved(t) ? null : approve,
    reject: isRejected(t) ? null : reject,
    expand: toggle,
  }

  return card
}

// ui-14: run a triage verb on a card node. Returns true if it did something.
// Centralized so the keydown handler in main.js stays declarative.
export function triageCard(action, card) {
  if (!card || !card._triage) return false
  const fn = card._triage[action]
  if (typeof fn !== 'function') return false
  fn()
  return true
}

// ui-14: the ordered, currently-rendered task cards for j/k focus navigation.
export function orderedTaskCards() {
  const list = getEl('taskList')
  if (!list) return []
  return Array.from(list.querySelectorAll('.task-card'))
}

// ui-14: given the current activeElement, move focus to the next/prev card.
// If nothing is focused yet, the first card takes focus. Wraps at the ends.
export function moveCardFocus(direction, activeEl) {
  const cards = orderedTaskCards()
  if (cards.length === 0) return null
  const current = activeEl && activeEl.closest ? activeEl.closest('.task-card') : null
  let idx = current ? cards.indexOf(current) : -1
  if (idx === -1) {
    idx = direction === 'prev' ? cards.length - 1 : 0
  } else {
    idx = direction === 'next' ? (idx + 1) % cards.length : (idx - 1 + cards.length) % cards.length
  }
  const target = cards[idx]
  if (target && typeof target.focus === 'function') target.focus()
  return target
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
