import { defineConfig } from 'vite'

// UI.2a — the light build seam for the SOW-to-Jira review UI.
//
// The Python/FastAPI backend serves the BUILT frontend from `ui/dist` under its
// existing `/static` mount, so built asset URLs must be prefixed with `/static/`
// (base). UI.2b split the monolithic IIFE into an ES module graph under
// `ui/src/` (entry `ui/src/main.js`); Vite resolves + bundles + hashes it.
//
// Dev (`npm run dev` / `make ui-dev`): Vite serves `ui/` with HMR and proxies
// `/api` to the uvicorn backend on :8000.
// Prod (`npm run build` / `make ui`): emits `ui/dist`, which FastAPI serves.
export default defineConfig({
  root: 'ui',
  base: '/static/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
