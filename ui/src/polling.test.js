// ui/src/polling.test.js — UI.3 (ui-9)
//
// error_class → (severity chip, primary recovery action) mapping for the shared
// polling failure branch. The backend ProcessingStatus.error_class carries the
// ErrorClass enum (TRANSIENT / USER_FIXABLE / TERMINAL). Each maps to a distinct
// recovery: Retry / Open Settings / Dismiss — and a WCAG-1.4.1-safe text chip
// (label, not colour-only). Unknown/missing falls back to a neutral Dismiss.
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { errorRecovery, ERROR_CLASS, renderFailureRecovery, clearFailureRecovery } from './polling.js'

describe('errorRecovery mapping (error_class → chip + action)', () => {
  it('TRANSIENT → "Retryable" chip + Retry action', () => {
    const r = errorRecovery(ERROR_CLASS.TRANSIENT)
    expect(r.chipLabel).toBe('Retryable')
    expect(r.chipClass).toBe('sev-transient')
    expect(r.action).toBe('retry')
    expect(r.actionLabel).toBe('Retry')
  })

  it('USER_FIXABLE → "Check credentials" chip + Open Settings action', () => {
    const r = errorRecovery(ERROR_CLASS.USER_FIXABLE)
    expect(r.chipLabel).toBe('Check credentials')
    expect(r.chipClass).toBe('sev-fixable')
    expect(r.action).toBe('open_settings')
    expect(r.actionLabel).toBe('Open Settings')
  })

  it('TERMINAL → "Failed" chip + Dismiss action', () => {
    const r = errorRecovery(ERROR_CLASS.TERMINAL)
    expect(r.chipLabel).toBe('Failed')
    expect(r.chipClass).toBe('sev-terminal')
    expect(r.action).toBe('dismiss')
    expect(r.actionLabel).toBe('Dismiss')
  })

  it('unknown error_class falls back to a neutral Dismiss', () => {
    const r = errorRecovery('SOMETHING_ELSE')
    expect(r.chipLabel).toBe('Failed')
    expect(r.chipClass).toBe('sev-terminal')
    expect(r.action).toBe('dismiss')
  })

  it('missing / null error_class falls back to a neutral Dismiss', () => {
    for (const v of [undefined, null, '']) {
      const r = errorRecovery(v)
      expect(r.action).toBe('dismiss')
      expect(r.chipLabel).toBe('Failed')
    }
  })

  it('is case-insensitive to the backend enum casing', () => {
    expect(errorRecovery('transient').action).toBe('retry')
    expect(errorRecovery('user_fixable').action).toBe('open_settings')
    expect(errorRecovery('terminal').action).toBe('dismiss')
  })
})

describe('renderFailureRecovery (DOM: chip above message + primary action)', () => {
  let card, message

  beforeEach(() => {
    document.body.innerHTML = ''
    card = document.createElement('div')
    card.className = 'progress-card'
    message = document.createElement('p')
    message.id = 'progressMessage'
    message.textContent = 'Error: boom'
    card.appendChild(message)
    document.body.appendChild(card)
  })

  it('inserts a text-labelled chip immediately before the message (not colour-only)', () => {
    renderFailureRecovery(ERROR_CLASS.TRANSIENT, { message }, {})
    const chip = card.querySelector('.sev-chip')
    expect(chip).not.toBeNull()
    // WCAG 1.4.1 — status is conveyed by TEXT, not colour alone.
    expect(chip.textContent).toBe('Retryable')
    expect(chip.classList.contains('sev-transient')).toBe(true)
    // Chip must sit ABOVE the failure message.
    expect(chip.nextElementSibling).toBe(message)
  })

  it('re-rendering replaces the old chip (no duplicates)', () => {
    renderFailureRecovery(ERROR_CLASS.TRANSIENT, { message }, {})
    renderFailureRecovery(ERROR_CLASS.TERMINAL, { message }, {})
    const chips = card.querySelectorAll('.sev-chip')
    expect(chips.length).toBe(1)
    expect(chips[0].textContent).toBe('Failed')
    expect(chips[0].classList.contains('sev-terminal')).toBe(true)
  })

  it('TRANSIENT wires the primary button to onRetry', () => {
    const onRetry = vi.fn()
    const onOpenSettings = vi.fn()
    const onDismiss = vi.fn()
    const btn = document.createElement('button')
    renderFailureRecovery(
      ERROR_CLASS.TRANSIENT,
      { message, primaryBtn: btn },
      { onRetry, onOpenSettings, onDismiss }
    )
    expect(btn.textContent).toBe('Retry')
    btn.click()
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(onOpenSettings).not.toHaveBeenCalled()
    expect(onDismiss).not.toHaveBeenCalled()
  })

  it('USER_FIXABLE wires the primary button to onOpenSettings', () => {
    const onRetry = vi.fn()
    const onOpenSettings = vi.fn()
    const btn = document.createElement('button')
    renderFailureRecovery(
      ERROR_CLASS.USER_FIXABLE,
      { message, primaryBtn: btn },
      { onRetry, onOpenSettings }
    )
    expect(btn.textContent).toBe('Open Settings')
    btn.click()
    expect(onOpenSettings).toHaveBeenCalledTimes(1)
    expect(onRetry).not.toHaveBeenCalled()
  })

  it('TERMINAL wires the primary button to onDismiss', () => {
    const onDismiss = vi.fn()
    const btn = document.createElement('button')
    renderFailureRecovery(ERROR_CLASS.TERMINAL, { message, primaryBtn: btn }, { onDismiss })
    expect(btn.textContent).toBe('Dismiss')
    btn.click()
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('unknown class falls back to a Dismiss-wired button', () => {
    const onDismiss = vi.fn()
    const btn = document.createElement('button')
    renderFailureRecovery(undefined, { message, primaryBtn: btn }, { onDismiss })
    expect(btn.textContent).toBe('Dismiss')
    btn.click()
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('after clear (a later success), clicking the reused button does NOT fire the recovery action', () => {
    // Regression: the recovery listener binds once for the button's lifetime.
    // A prior failure wires it; a subsequent successful run calls
    // clearFailureRecovery. Clicking the now-plain Dismiss must NOT invoke
    // onDismiss (which resets the session and would wipe the loaded tasks) —
    // only main.js's own overlay-hide listener should run.
    const onDismiss = vi.fn()
    const btn = document.createElement('button')
    // Run A fails → binds the recovery listener.
    renderFailureRecovery(ERROR_CLASS.TERMINAL, { message, primaryBtn: btn }, { onDismiss })
    // Run B succeeds → clears recovery on the reused button.
    clearFailureRecovery({ message, primaryBtn: btn })
    btn.click()
    expect(onDismiss).not.toHaveBeenCalled()
  })
})
