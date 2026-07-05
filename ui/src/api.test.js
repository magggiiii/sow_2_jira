import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
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
  apiFetch,
  testJiraConnection,
  setOnUnauthorized,
  __resetCsrfCache,
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

// ---------------------------------------------------------------------------
// FE-2 (2.6d) — auth-aware fetch wrapper + Jira "Test connection"
// ---------------------------------------------------------------------------

// Clear the sow_csrf cookie between tests so cookie-derived token cases are
// deterministic (jsdom persists document.cookie across tests otherwise).
function clearCsrfCookie() {
  document.cookie = 'sow_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
}

describe('FE-2 apiFetch — auth-aware fetch wrapper', () => {
  beforeEach(() => {
    __resetCsrfCache()
    clearCsrfCookie()
    setOnUnauthorized(null)
  })
  afterEach(() => {
    vi.restoreAllMocks()
    clearCsrfCookie()
  })

  it('GET requests do not attach a CSRF header and still send credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch('/api/status', { method: 'GET' })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/status')
    expect(opts.credentials).toBe('same-origin')
    // No CSRF header on a safe method.
    const headers = new Headers(opts.headers)
    expect(headers.has('X-CSRF-Token')).toBe(false)
    // And we never went looking for a token endpoint for a GET.
    expect(fetchMock.mock.calls.every(([u]) => u !== '/api/csrf')).toBe(true)
  })

  it('attaches X-CSRF-Token from the sow_csrf cookie on a POST + sends credentials', async () => {
    document.cookie = 'sow_csrf=cookie-tok-123; path=/'
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch('/api/push', { method: 'POST', body: '{}' })

    // The cookie token is used directly — no /api/csrf round-trip needed.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/push')
    expect(opts.credentials).toBe('same-origin')
    const headers = new Headers(opts.headers)
    expect(headers.get('X-CSRF-Token')).toBe('cookie-tok-123')
  })

  it('fetches /api/csrf once when no cookie is present, then reuses the token', async () => {
    const fetchMock = vi.fn().mockImplementation((url) => {
      if (url === '/api/csrf') {
        // The mint endpoint also sets the cookie server-side; simulate that.
        document.cookie = 'sow_csrf=minted-tok; path=/'
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ csrf_token: 'minted-tok' }) })
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
    })
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch('/api/push', { method: 'POST' })
    await apiFetch('/api/tasks', { method: 'POST' })

    const csrfCalls = fetchMock.mock.calls.filter(([u]) => u === '/api/csrf')
    expect(csrfCalls.length).toBe(1) // cached after the first mint
    const postCalls = fetchMock.mock.calls.filter(([u]) => u !== '/api/csrf')
    postCalls.forEach(([, opts]) => {
      expect(new Headers(opts.headers).get('X-CSRF-Token')).toBe('minted-tok')
    })
  })

  it('a 401 response invokes the onUnauthorized handler exactly once', async () => {
    const onUnauth = vi.fn()
    setOnUnauthorized(onUnauth)
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)

    const res = await apiFetch('/api/push', { method: 'POST' })

    expect(onUnauth).toHaveBeenCalledTimes(1)
    // The response is still returned so callers can decide what to do.
    expect(res.status).toBe(401)
  })

  it('graceful degrade: POST works when /api/csrf is unavailable (no cookie, no endpoint)', async () => {
    const fetchMock = vi.fn().mockImplementation((url) => {
      if (url === '/api/csrf') {
        // Endpoint 404 (auth off / not wired) — must not blow up the POST.
        return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) })
    })
    vi.stubGlobal('fetch', fetchMock)

    const res = await apiFetch('/api/push', { method: 'POST' })
    expect(res.status).toBe(200)
    const postCall = fetchMock.mock.calls.find(([u]) => u !== '/api/csrf')
    // credentials always; no CSRF header since none was obtainable.
    expect(postCall[1].credentials).toBe('same-origin')
    expect(new Headers(postCall[1].headers).has('X-CSRF-Token')).toBe(false)
  })
})

describe('FE-2 testJiraConnection', () => {
  beforeEach(() => {
    __resetCsrfCache()
    clearCsrfCookie()
  })
  afterEach(() => {
    vi.restoreAllMocks()
    clearCsrfCookie()
  })

  it('POSTs /api/jira/test and returns the classified success result', async () => {
    const payload = { success: true, user: 'Ada Lovelace', server: 'https://x.atlassian.net' }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => payload })
    vi.stubGlobal('fetch', fetchMock)

    const result = await testJiraConnection()

    const postCall = fetchMock.mock.calls.find(([u]) => u === '/api/jira/test')
    expect(postCall).toBeTruthy()
    expect(postCall[1].method).toBe('POST')
    expect(postCall[1].credentials).toBe('same-origin')
    expect(result).toEqual(payload)
  })

  it('returns the classified failure result (user_fixable / transient)', async () => {
    const payload = { success: false, error: '401 Unauthorized', error_class: 'user_fixable' }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => payload })
    vi.stubGlobal('fetch', fetchMock)

    const result = await testJiraConnection()
    expect(result.success).toBe(false)
    expect(result.error_class).toBe('user_fixable')
    expect(result.error).toBe('401 Unauthorized')
  })
})
