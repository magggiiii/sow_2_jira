import { describe, it, expect } from 'vitest'
import {
  TASK_STATUS,
  computeStats,
  shouldShowTask,
  statusDisplay,
  containerLabelFor,
  childLabelFor,
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

describe('computeStats', () => {
  it('buckets tasks by status', () => {
    const tasks = [
      { status: 'APPROVED' },
      { status: 'APPROVED' },
      { status: 'REJECTED' },
      { status: 'PUSHED' },
      { status: 'OPEN' },
      { status: 'CLOSED' },
    ]
    const stats = computeStats(tasks)
    expect(stats.total).toBe(6)
    expect(stats.approved).toBe(2)
    expect(stats.rejected).toBe(1)
    expect(stats.pushed).toBe(1)
    // pending = total - approved - rejected - pushed
    expect(stats.pending).toBe(2)
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
  const on = { pending: true, approved: true, rejected: true, flagged: false }

  it('shows everything when all status filters are on and flagged is off', () => {
    expect(shouldShowTask({ status: 'CLOSED' }, on)).toBe(true)
    expect(shouldShowTask({ status: 'APPROVED' }, on)).toBe(true)
    expect(shouldShowTask({ status: 'REJECTED' }, on)).toBe(true)
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

  it('hides PUSHED only when both approved AND pending filters are off', () => {
    expect(shouldShowTask({ status: 'PUSHED' }, { ...on, approved: false })).toBe(true)
    expect(shouldShowTask({ status: 'PUSHED' }, { ...on, pending: false })).toBe(true)
    expect(
      shouldShowTask({ status: 'PUSHED' }, { ...on, approved: false, pending: false })
    ).toBe(false)
  })

  it('flagged-only mode hides tasks without flags', () => {
    const f = { ...on, flagged: true }
    expect(shouldShowTask({ status: 'OPEN', flags: [] }, f)).toBe(false)
    expect(shouldShowTask({ status: 'OPEN', flags: ['DUP'] }, f)).toBe(true)
    expect(shouldShowTask({ status: 'OPEN' }, f)).toBe(false)
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
