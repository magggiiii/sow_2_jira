import { describe, it, expect } from 'vitest'
import {
  TASK_STATUS,
  STATUS_BUCKET,
  bucketOf,
  computeStats,
  shouldShowTask,
  statusDisplay,
  containerLabelFor,
  childLabelFor,
  pushPreflight,
  decideSessionSwitch,
} from './api.js'

describe('api view-model — status enum', () => {
  it('exposes the canonical TaskStatus strings once', () => {
    expect(TASK_STATUS).toEqual({
      OPEN: 'OPEN',
      CLOSED: 'CLOSED',
      MERGED: 'MERGED',
      REJECTED: 'REJECTED',
      APPROVED: 'APPROVED',
      PUSHED: 'PUSHED',
    })
  })
})

describe('ui-15 — single status→bucket map (filter + stats agree)', () => {
  it('maps every TaskStatus to exactly one review bucket', () => {
    // Every enum value is present in the map, and only the four valid buckets.
    Object.values(TASK_STATUS).forEach((s) => {
      expect(STATUS_BUCKET[s]).toBeTruthy()
      expect(['pending', 'approved', 'rejected', 'pushed']).toContain(STATUS_BUCKET[s])
    })
    // The specific buckets (verified against models/schemas.py :: TaskStatus).
    expect(bucketOf('OPEN')).toBe('pending')
    expect(bucketOf('CLOSED')).toBe('pending')
    expect(bucketOf('MERGED')).toBe('pending')
    expect(bucketOf('APPROVED')).toBe('approved')
    expect(bucketOf('REJECTED')).toBe('rejected')
    expect(bucketOf('PUSHED')).toBe('pushed')
  })

  it('bucketOf falls back to pending for unknown/blank', () => {
    expect(bucketOf('WHATEVER')).toBe('pending')
    expect(bucketOf(undefined)).toBe('pending')
  })

  it('computeStats and the filter agree on the SAME bucket for every task', () => {
    // Regression for the old drift: for each status, if computeStats counts it in
    // bucket X, then turning off ONLY filter X must hide it, and turning off any
    // OTHER bucket must NOT hide it.
    const allOn = { pending: true, approved: true, rejected: true, pushed: true, flagged: false }
    Object.values(TASK_STATUS).forEach((status) => {
      const bucket = bucketOf(status)
      const stats = computeStats([{ status }])
      expect(stats[bucket]).toBe(1)

      const offThis = { ...allOn, [bucket]: false }
      expect(shouldShowTask({ status }, offThis)).toBe(false)

      // Off a DIFFERENT bucket → still shown.
      const other = ['pending', 'approved', 'rejected', 'pushed'].find((b) => b !== bucket)
      expect(shouldShowTask({ status }, { ...allOn, [other]: false })).toBe(true)
    })
  })
})

describe('computeStats', () => {
  it('buckets tasks by status', () => {
    const tasks = [
      { status: 'APPROVED' },
      { status: 'APPROVED' },
      { status: 'REJECTED' },
      { status: 'PUSHED' },
      { status: 'OPEN' },
      { status: 'CLOSED' },
      { status: 'MERGED' },
    ]
    const stats = computeStats(tasks)
    expect(stats.total).toBe(7)
    expect(stats.approved).toBe(2)
    expect(stats.rejected).toBe(1)
    expect(stats.pushed).toBe(1)
    // pending = OPEN + CLOSED + MERGED
    expect(stats.pending).toBe(3)
  })

  it('handles an empty list', () => {
    expect(computeStats([])).toEqual({
      total: 0,
      approved: 0,
      rejected: 0,
      pushed: 0,
      pending: 0,
    })
  })
})

describe('shouldShowTask filters', () => {
  const on = { pending: true, approved: true, rejected: true, pushed: true, flagged: false }

  it('shows everything when all status filters are on and flagged is off', () => {
    expect(shouldShowTask({ status: 'CLOSED' }, on)).toBe(true)
    expect(shouldShowTask({ status: 'APPROVED' }, on)).toBe(true)
    expect(shouldShowTask({ status: 'REJECTED' }, on)).toBe(true)
    expect(shouldShowTask({ status: 'PUSHED' }, on)).toBe(true)
  })

  it('hides CLOSED (pending bucket) when pending filter is off', () => {
    const f = { ...on, pending: false }
    expect(shouldShowTask({ status: 'CLOSED' }, f)).toBe(false)
  })

  it('hides APPROVED when approved filter is off', () => {
    const f = { ...on, approved: false }
    expect(shouldShowTask({ status: 'APPROVED' }, f)).toBe(false)
  })

  it('hides REJECTED when rejected filter is off', () => {
    const f = { ...on, rejected: false }
    expect(shouldShowTask({ status: 'REJECTED' }, f)).toBe(false)
  })

  it('ui-15: hides PUSHED when the dedicated pushed filter is off', () => {
    expect(shouldShowTask({ status: 'PUSHED' }, { ...on, pushed: false })).toBe(false)
    // ...and toggling approved/pending no longer affects PUSHED (own bucket now).
    expect(shouldShowTask({ status: 'PUSHED' }, { ...on, approved: false })).toBe(true)
    expect(shouldShowTask({ status: 'PUSHED' }, { ...on, pending: false })).toBe(true)
  })

  it('flagged-only mode hides tasks without flags', () => {
    const f = { ...on, flagged: true }
    expect(shouldShowTask({ status: 'OPEN', flags: [] }, f)).toBe(false)
    expect(shouldShowTask({ status: 'OPEN', flags: ['DUP'] }, f)).toBe(true)
    expect(shouldShowTask({ status: 'OPEN' }, f)).toBe(false)
  })
})

describe('ui-16 — pushPreflight predicate', () => {
  const ok = { activeSessionId: 'run-1', approvedCount: 3, jiraConfigured: true }

  it('enables push only when all three conditions hold', () => {
    const r = pushPreflight(ok)
    expect(r.enabled).toBe(true)
    expect(r.reason).toBe('')
  })

  it('disables (with a reason) when no session is active', () => {
    const r = pushPreflight({ ...ok, activeSessionId: null })
    expect(r.enabled).toBe(false)
    expect(r.reason).toMatch(/session/i)
  })

  it('disables when there are zero approved tasks', () => {
    const r = pushPreflight({ ...ok, approvedCount: 0 })
    expect(r.enabled).toBe(false)
    expect(r.reason).toMatch(/approve/i)
  })

  it('disables when Jira creds are not saved', () => {
    const r = pushPreflight({ ...ok, jiraConfigured: false })
    expect(r.enabled).toBe(false)
    expect(r.reason).toMatch(/jira/i)
  })

  it('is safe on an empty/undefined input', () => {
    expect(pushPreflight().enabled).toBe(false)
    expect(pushPreflight({}).enabled).toBe(false)
  })
})

describe('ui-18 — decideSessionSwitch (running-state)', () => {
  it("returns 'resume' when the target session is still running", () => {
    expect(decideSessionSwitch({ is_running: true })).toBe('resume')
  })
  it("returns 'load' when the target session is idle/complete", () => {
    expect(decideSessionSwitch({ is_running: false })).toBe('load')
    expect(decideSessionSwitch({})).toBe('load')
    expect(decideSessionSwitch(null)).toBe('load')
  })
})

describe('statusDisplay', () => {
  it('returns a label + css var for terminal-ish statuses', () => {
    expect(statusDisplay('APPROVED').text).toContain('Approved')
    expect(statusDisplay('APPROVED').color).toBe('var(--success)')
    expect(statusDisplay('REJECTED').color).toBe('var(--error)')
    expect(statusDisplay('PUSHED').color).toBe('var(--accent-primary)')
  })

  it('returns null for statuses without an inline label (OPEN/CLOSED)', () => {
    expect(statusDisplay('OPEN')).toBe(null)
    expect(statusDisplay('CLOSED')).toBe(null)
  })
})

describe('hierarchy labels', () => {
  it('maps hierarchy to container labels', () => {
    expect(containerLabelFor('story_subtask')).toBe('Story')
    expect(containerLabelFor('epic_task')).toBe('Epic')
    expect(containerLabelFor('flat')).toBe('Section')
  })
  it('maps hierarchy to child labels', () => {
    expect(childLabelFor('story_subtask')).toBe('Sub-task')
    expect(childLabelFor('epic_task')).toBe('Task')
    expect(childLabelFor('flat')).toBe('Task')
  })
})
