// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// motion drives helix/newton via WAAPI, which jsdom doesn't implement — mock the
// engine so we can assert the loaders WIRE it correctly (call counts + cleanup)
// without running real animations. Real smoothness is verified in the browser.
vi.mock('motion', () => ({ animate: vi.fn(() => ({ stop: vi.fn() })) }))

import { animate } from 'motion'
import { createLoader, mountLoader, LOADER_TYPES } from './loaders.js'

beforeEach(() => {
  vi.clearAllMocks()
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('loaders — factory', () => {
  it('exports the three supported types', () => {
    expect(LOADER_TYPES).toEqual(['scramble', 'helix', 'newton'])
  })

  it('throws on an unknown type', () => {
    expect(() => createLoader('does-not-exist')).toThrow(/Unknown loader type/)
  })

  it('every variant exposes a uniform handle', () => {
    for (const type of LOADER_TYPES) {
      const loader = createLoader(type)
      expect(loader.el).toBeInstanceOf(HTMLElement)
      expect(typeof loader.start).toBe('function')
      expect(typeof loader.stop).toBe('function')
      expect(typeof loader.destroy).toBe('function')
      expect(loader.el.className).toContain(`loader-${type}`)
      expect(loader.el.getAttribute('role')).toBe('status')
    }
  })
})

describe('loaders — helix / newton (motion-driven)', () => {
  it('helix renders 7 rows of paired dots', () => {
    const { el } = createLoader('helix')
    expect(el.querySelectorAll('.loader-helix-row')).toHaveLength(7)
    expect(el.querySelectorAll('.loader-helix-dot')).toHaveLength(14)
  })

  it('newton renders 5 balls', () => {
    const { el } = createLoader('newton')
    expect(el.querySelectorAll('.loader-newton-ball')).toHaveLength(5)
  })

  it('helix start() animates all 14 dots and destroy() stops every animation', () => {
    const loader = createLoader('helix')
    loader.start()
    expect(animate).toHaveBeenCalledTimes(14)
    loader.destroy()
    expect(animate.mock.results.every((r) => r.value.stop.mock.calls.length === 1)).toBe(true)
  })

  it('newton start() animates the two end balls', () => {
    const loader = createLoader('newton')
    loader.start()
    expect(animate).toHaveBeenCalledTimes(2)
    loader.destroy()
    expect(animate.mock.results.every((r) => r.value.stop.mock.calls.length === 1)).toBe(true)
  })

  it('start() is idempotent — no duplicate motion animations', () => {
    const loader = createLoader('helix')
    loader.start()
    loader.start()
    expect(animate).toHaveBeenCalledTimes(14)
    loader.destroy()
  })

  it('destroy removes the element from its parent', () => {
    const host = document.createElement('div')
    const loader = createLoader('newton')
    host.appendChild(loader.el)
    expect(host.children).toHaveLength(1)
    loader.destroy()
    expect(host.children).toHaveLength(0)
  })
})

describe('loaders — scramble (timer)', () => {
  it('reveals the label over time and loops', () => {
    vi.useFakeTimers()
    const loader = createLoader('scramble', { label: 'LOADING' })
    loader.start()
    expect(loader.el.querySelector('.loader-scramble-text').textContent).toHaveLength(7)
    vi.advanceTimersByTime(55 * 8 + 5)
    expect(loader.el.querySelector('.loader-scramble-text').textContent).toBe('LOADING')
    loader.stop()
  })

  it('stop() halts updates', () => {
    vi.useFakeTimers()
    const loader = createLoader('scramble', { label: 'HI' })
    loader.start()
    loader.stop()
    const frozen = loader.el.querySelector('.loader-scramble-text').textContent
    vi.advanceTimersByTime(55 * 10)
    expect(loader.el.querySelector('.loader-scramble-text').textContent).toBe(frozen)
  })

  it('start() is idempotent (single interval)', () => {
    vi.useFakeTimers()
    const spy = vi.spyOn(globalThis, 'setInterval')
    const loader = createLoader('scramble', { label: 'X' })
    loader.start()
    loader.start()
    expect(spy).toHaveBeenCalledTimes(1)
    loader.destroy()
  })
})

describe('loaders — prefers-reduced-motion', () => {
  it('scramble shows the plain label and starts no timer', () => {
    vi.useFakeTimers()
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }))
    const setInterval = vi.spyOn(globalThis, 'setInterval')
    const loader = createLoader('scramble', { label: 'LOADING' })
    loader.start()
    expect(loader.el.querySelector('.loader-scramble-text').textContent).toBe('LOADING')
    expect(setInterval).not.toHaveBeenCalled()
    vi.advanceTimersByTime(55 * 20)
    expect(loader.el.querySelector('.loader-scramble-text').textContent).toBe('LOADING')
  })

  it('helix / newton stay static (no motion animations) under reduced motion', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }))
    createLoader('helix').start()
    createLoader('newton').start()
    expect(animate).not.toHaveBeenCalled()
  })
})

describe('loaders — mountLoader', () => {
  it('appends the loader to a container and returns the handle', () => {
    const host = document.createElement('div')
    const loader = mountLoader(host, 'helix')
    expect(host.contains(loader.el)).toBe(true)
    loader.destroy()
    expect(host.contains(loader.el)).toBe(false)
  })
})
