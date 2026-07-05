// ui/src/render.test.js — UI.4a (ui-11, ui-12, ui-13)
//
// Covers the review-flow structural core:
//   (a) ui-13 — a <script>-titled section renders INERT (XSS guard).
//   (b) ui-12 — a collapsible card header is an accessible button
//       (role/tabindex/aria-expanded) that toggles on Enter AND Space,
//       and shows a status TEXT label beside the dot (WCAG 1.4.1).
//   (c) ui-11 — an in-place patch of one card preserves another card's
//       expanded state (patch does not innerHTML-wipe the list).
import { describe, it, expect, beforeEach } from 'vitest'
import { renderTasks, patchTaskCard, escapeHtml } from './render.js'
import { $taskData, setTaskData } from './state.js'
import { markTasksLoaded } from './render.js'

// Minimal DOM scaffold: the containers + the <template> render.js clones from.
// Kept in sync with ui/index.html #taskCardTemplate so the module is testable
// without loading the full page.
function mountDom() {
  document.body.innerHTML = `
    <div id="toastContainer"></div>
    <div id="showingCount"></div>
    <select id="jiraHierarchy"><option value="epic_task" selected>epic_task</option></select>
    <input type="checkbox" id="filterPending" checked>
    <input type="checkbox" id="filterApproved" checked>
    <input type="checkbox" id="filterRejected">
    <input type="checkbox" id="filterFlagged">
    <div id="taskList"></div>
    <template id="taskCardTemplate">
      <div class="task-card">
        <div class="task-header">
          <div class="task-title-area">
            <div class="status-indicator"></div>
            <span class="task-title-text"></span>
            <div class="task-badges"></div>
          </div>
          <svg class="chevron"><polyline points="6 9 12 15 18 9"></polyline></svg>
        </div>
        <div class="task-body">
          <div class="task-body-content">
            <div class="task-details">
              <div class="source-ref"></div>
              <input type="text" class="input-field task-edit-title">
              <textarea class="input-field task-edit-desc"></textarea>
              <textarea class="input-field task-edit-usecase"></textarea>
              <textarea class="input-field task-edit-ac"></textarea>
              <textarea class="input-field task-edit-cc"></textarea>
              <textarea class="input-field task-edit-del"></textarea>
            </div>
            <div class="task-actions">
              <button class="btn btn-success btn-approve">Approve</button>
              <button class="btn btn-danger btn-reject">Reject</button>
              <button class="btn btn-outline btn-save">Save Edits</button>
              <div class="task-status-text"></div>
            </div>
          </div>
        </div>
      </div>
    </template>
  `
}

function keydown(el, key) {
  const ev = new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
  el.dispatchEvent(ev)
}

function makeTask(over = {}) {
  return {
    id: 't1',
    title: 'A task',
    status: 'OPEN',
    confidence: 0.9,
    flags: [],
    short_description: '',
    use_case: '',
    acceptance_criteria: [],
    considerations_constraints: [],
    deliverables: [],
    source_refs: [{ section_title: 'General', page_start: 1, page_end: 2 }],
    ...over,
  }
}

beforeEach(() => {
  mountDom()
  markTasksLoaded()
})

describe('ui-13 — XSS guard on section titles', () => {
  it('renders a <script>-titled section inert (no script node, escaped text)', () => {
    const evil = '<script>window.__pwned = true</script>'
    setTaskData({
      tasks: [makeTask({ source_refs: [{ section_title: evil, page_start: 1, page_end: 2 }] })],
      config: {},
    })
    renderTasks(() => {})

    const taskList = document.getElementById('taskList')
    // No live <script> element should be injected by the section title.
    expect(taskList.querySelector('script')).toBeNull()
    expect(window.__pwned).toBeUndefined()
    // The literal text is still present (escaped, as text content).
    expect(taskList.textContent).toContain('<script>')
  })

  it('escapeHtml neutralizes angle brackets and quotes', () => {
    expect(escapeHtml('<b>&"\'')).toBe('&lt;b&gt;&amp;&quot;&#39;')
  })
})

describe('ui-12 — accessible collapsibles', () => {
  it('header is a keyboard-operable button with aria wiring', () => {
    setTaskData({ tasks: [makeTask()], config: {} })
    renderTasks(() => {})

    const header = document.querySelector('.task-card .task-header')
    expect(header.getAttribute('role')).toBe('button')
    expect(header.getAttribute('tabindex')).toBe('0')
    expect(header.getAttribute('aria-expanded')).toBe('false')

    const controls = header.getAttribute('aria-controls')
    expect(controls).toBeTruthy()
    expect(document.getElementById(controls)).not.toBeNull()
  })

  it('Enter and Space both toggle expansion', () => {
    setTaskData({ tasks: [makeTask()], config: {} })
    renderTasks(() => {})

    const card = document.querySelector('.task-card')
    const header = card.querySelector('.task-header')

    expect(card.classList.contains('expanded')).toBe(false)
    keydown(header, 'Enter')
    expect(card.classList.contains('expanded')).toBe(true)
    expect(header.getAttribute('aria-expanded')).toBe('true')

    keydown(header, ' ')
    expect(card.classList.contains('expanded')).toBe(false)
    expect(header.getAttribute('aria-expanded')).toBe('false')
  })

  it('collapsed header shows a status TEXT label beside the dot', () => {
    setTaskData({ tasks: [makeTask({ status: 'APPROVED' })], config: {} })
    renderTasks(() => {})

    const header = document.querySelector('.task-card .task-header')
    const label = header.querySelector('.status-label')
    expect(label).not.toBeNull()
    expect(label.textContent.toLowerCase()).toContain('approved')
  })
})

describe('ui-11 — optimistic in-place patch', () => {
  it('patching one card preserves another card expanded state (no list wipe)', () => {
    setTaskData({
      tasks: [makeTask({ id: 't1', title: 'One' }), makeTask({ id: 't2', title: 'Two' })],
      config: {},
    })
    renderTasks(() => {})

    // Expand card t2 by hand.
    const cardTwo = document.querySelector('[data-task-id="t2"]')
    cardTwo.classList.add('expanded')
    expect(cardTwo.classList.contains('expanded')).toBe(true)

    // Grab a stable reference to t2's DOM node to prove it is NOT recreated.
    const cardTwoRef = cardTwo

    // Patch t1 only.
    patchTaskCard({ ...makeTask({ id: 't1', title: 'One (edited)' }) }, () => {})

    // t2 must still exist, be the SAME node, and still be expanded.
    const cardTwoAfter = document.querySelector('[data-task-id="t2"]')
    expect(cardTwoAfter).toBe(cardTwoRef)
    expect(cardTwoAfter.classList.contains('expanded')).toBe(true)

    // t1 must reflect the edit.
    const cardOneAfter = document.querySelector('[data-task-id="t1"]')
    expect(cardOneAfter.querySelector('.task-title-text').textContent).toBe('One (edited)')

    // The store is updated in place for t1.
    const stored = $taskData.get().tasks.find((t) => t.id === 't1')
    expect(stored.title).toBe('One (edited)')
  })

  it('patch preserves the patched card own expanded state', () => {
    setTaskData({ tasks: [makeTask({ id: 't1' })], config: {} })
    renderTasks(() => {})

    const card = document.querySelector('[data-task-id="t1"]')
    card.classList.add('expanded')

    patchTaskCard({ ...makeTask({ id: 't1', title: 'still open' }) }, () => {})

    const after = document.querySelector('[data-task-id="t1"]')
    expect(after.classList.contains('expanded')).toBe(true)
  })
})
