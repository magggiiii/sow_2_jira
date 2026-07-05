// ui/src/modals.test.js — UI.6 (ui-21) modal accessibility helpers
//
// Exercises the reusable dialog a11y layer that modals.js exposes:
//   openModal / closeModal + focus trap + Escape + guarded backdrop click.
// jsdom-only; builds a minimal overlay/card fixture that mirrors the real
// #settingsModal shape (a `.progress-overlay` backdrop wrapping a
// `.progress-card` with a heading + focusable controls).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { openModal, closeModal, isModalOpen, showConfirm } from './modals.js'

function buildFixture() {
  document.body.innerHTML = `
    <button id="opener">Open</button>
    <div id="testModal" class="progress-overlay" style="display: none;">
      <div class="progress-card">
        <h2>Test Dialog Title</h2>
        <input id="firstField" type="text" />
        <button id="midBtn">Middle</button>
        <button id="lastBtn">Last</button>
      </div>
    </div>
  `
}

function pressKey(target, key, opts = {}) {
  const ev = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...opts,
  })
  target.dispatchEvent(ev)
  return ev
}

describe('modal a11y helpers', () => {
  let modal
  let opener

  beforeEach(() => {
    buildFixture()
    modal = document.getElementById('testModal')
    opener = document.getElementById('opener')
    opener.focus()
  })

  afterEach(() => {
    // Ensure listeners/handles are torn down between tests.
    if (isModalOpen(modal)) closeModal(modal)
    document.body.innerHTML = ''
  })

  it('open sets dialog ARIA attributes and moves focus inside', () => {
    openModal(modal)
    expect(modal.style.display).toBe('flex')
    const card = modal.querySelector('.progress-card')
    expect(card.getAttribute('role')).toBe('dialog')
    expect(card.getAttribute('aria-modal')).toBe('true')
    // aria-labelledby points at the heading, and the heading got an id.
    const heading = card.querySelector('h2')
    expect(heading.id).toBeTruthy()
    expect(card.getAttribute('aria-labelledby')).toBe(heading.id)
    // Focus moved into the dialog.
    expect(card.contains(document.activeElement)).toBe(true)
  })

  it('Escape closes the modal', () => {
    openModal(modal)
    expect(isModalOpen(modal)).toBe(true)
    pressKey(document, 'Escape')
    expect(isModalOpen(modal)).toBe(false)
    expect(modal.style.display).toBe('none')
  })

  it('focus trap wraps Tab from last focusable to first', () => {
    openModal(modal)
    const first = document.getElementById('firstField')
    const last = document.getElementById('lastBtn')
    last.focus()
    expect(document.activeElement).toBe(last)
    const ev = pressKey(document, 'Tab')
    expect(ev.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(first)
  })

  it('focus trap wraps Shift+Tab from first focusable to last', () => {
    openModal(modal)
    const first = document.getElementById('firstField')
    const last = document.getElementById('lastBtn')
    first.focus()
    const ev = pressKey(document, 'Tab', { shiftKey: true })
    expect(ev.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(last)
  })

  it('close restores focus to the opener', () => {
    opener.focus()
    openModal(modal)
    expect(document.activeElement).not.toBe(opener)
    closeModal(modal)
    expect(document.activeElement).toBe(opener)
  })

  it('backdrop click closes the modal but inner content click does not', () => {
    openModal(modal)
    const card = modal.querySelector('.progress-card')
    // Click inside the card -> stays open.
    card.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(isModalOpen(modal)).toBe(true)
    // Click on the backdrop itself -> closes.
    modal.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(isModalOpen(modal)).toBe(false)
  })

  it('closeModal invokes an optional onClose callback', () => {
    const onClose = vi.fn()
    openModal(modal, { onClose })
    closeModal(modal)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('ui-14/ui-16 — styled showConfirm (replaces native confirm)', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('renders a themed dialog with the given title + labels', async () => {
    const p = showConfirm({ title: 'Push to Jira?', message: 'Push 3 tasks?', confirmLabel: 'Push' })
    const overlay = document.querySelector('.progress-overlay')
    expect(overlay).not.toBeNull()
    expect(overlay.querySelector('h2').textContent).toBe('Push to Jira?')
    expect(overlay.querySelector('.confirm-message').textContent).toBe('Push 3 tasks?')
    const confirmBtn = Array.from(overlay.querySelectorAll('button')).find(
      (b) => b.textContent === 'Push'
    )
    expect(confirmBtn).toBeTruthy()
    confirmBtn.click()
    await expect(p).resolves.toBe(true)
    // dialog is torn down after resolution.
    expect(document.querySelector('.progress-overlay')).toBeNull()
  })

  it('resolves false when Cancel is clicked and removes the dialog', async () => {
    const p = showConfirm({ title: 'Delete?', confirmLabel: 'Delete', cancelLabel: 'Cancel' })
    const overlay = document.querySelector('.progress-overlay')
    const cancelBtn = Array.from(overlay.querySelectorAll('button')).find(
      (b) => b.textContent === 'Cancel'
    )
    cancelBtn.click()
    await expect(p).resolves.toBe(false)
    expect(document.querySelector('.progress-overlay')).toBeNull()
  })

  it('resolves false on Escape (cancel), never double-resolving', async () => {
    const p = showConfirm({ title: 'Sure?' })
    pressKey(document, 'Escape')
    await expect(p).resolves.toBe(false)
    expect(document.querySelector('.progress-overlay')).toBeNull()
  })
})
