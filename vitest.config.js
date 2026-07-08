import { defineConfig } from 'vitest/config'

// UI.2b — unit tests for the extracted frontend modules (state store + api
// view-model). Tests live next to the code under ui/src/*.test.js. Non-watch
// (`vitest run`) so CI stays deterministic. jsdom gives the render/DOM-touching
// modules a document to work against when needed.
export default defineConfig({
  test: {
    include: ['ui/**/*.test.js'],
    environment: 'jsdom',
    globals: true,
    // Auto-restore vi.stubGlobal('fetch', ...) between tests. Without this,
    // restoreAllMocks() does NOT reset stubbed globals, so a stubbed global
    // fetch leaks across test files within a worker and can intermittently
    // false-green the security-critical XSS-escaping / CSRF-header specs.
    unstubGlobals: true,
  },
})
