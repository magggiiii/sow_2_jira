import { describe, it, expect, beforeEach } from 'vitest'
import {
  $activeSessionId,
  $taskData,
  $pollStatus,
  setActiveSession,
  clearActiveSession,
  setTaskData,
  resetTaskData,
  setPollStatus,
} from './state.js'

describe('state store', () => {
  beforeEach(() => {
    // reset to defaults between tests
    clearActiveSession()
    resetTaskData()
    setPollStatus({ isRunning: false, pollErrorCount: 0 })
    // clear any persistence side-effects
    sessionStorage.clear()
  })

  it('defaults are sane', () => {
    expect($activeSessionId.get()).toBe(null)
    expect($taskData.get()).toEqual({ tasks: [], config: {} })
    expect($pollStatus.get().isRunning).toBe(false)
  })

  it('setActiveSession updates the atom and persists to sessionStorage', () => {
    setActiveSession('run-123')
    expect($activeSessionId.get()).toBe('run-123')
    expect(sessionStorage.getItem('activeSessionId')).toBe('run-123')
  })

  it('clearActiveSession nulls the atom and removes the persisted key', () => {
    setActiveSession('run-123')
    clearActiveSession()
    expect($activeSessionId.get()).toBe(null)
    expect(sessionStorage.getItem('activeSessionId')).toBe(null)
  })

  it('setTaskData replaces the whole task payload', () => {
    const payload = { tasks: [{ id: 't1', status: 'OPEN' }], config: { max_nodes: 200 } }
    setTaskData(payload)
    expect($taskData.get()).toEqual(payload)
  })

  it('resetTaskData restores the empty default shape', () => {
    setTaskData({ tasks: [{ id: 't1' }], config: { x: 1 } })
    resetTaskData()
    expect($taskData.get()).toEqual({ tasks: [], config: {} })
  })

  it('setPollStatus merges partial updates', () => {
    setPollStatus({ isRunning: true })
    expect($pollStatus.get().isRunning).toBe(true)
    setPollStatus({ pollErrorCount: 2 })
    expect($pollStatus.get()).toEqual({ isRunning: true, pollErrorCount: 2 })
  })

  it('notifies subscribers on change (reactivity)', () => {
    const seen = []
    const unsub = $activeSessionId.subscribe((v) => seen.push(v))
    setActiveSession('a')
    setActiveSession('b')
    unsub()
    setActiveSession('c') // should NOT be observed after unsubscribe
    expect(seen).toContain('a')
    expect(seen).toContain('b')
    expect(seen).not.toContain('c')
  })
})
