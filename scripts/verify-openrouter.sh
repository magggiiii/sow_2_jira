#!/usr/bin/env bash
# Smoke-test the OpenRouter integration end-to-end.
#
# Prereqs:
#   1. Server running locally: `make ui` (port 8000) or `s2j` (Docker)
#   2. Export OPENROUTER_API_KEY=sk-or-...
#
# Usage:
#   OPENROUTER_API_KEY=sk-or-... bash scripts/verify-openrouter.sh
#   OPENROUTER_API_KEY=sk-or-... bash scripts/verify-openrouter.sh http://localhost:8000

set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"

if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "ERROR: OPENROUTER_API_KEY env var is required." >&2
  echo "Usage: OPENROUTER_API_KEY=sk-or-... bash scripts/verify-openrouter.sh [base_url]" >&2
  exit 1
fi

echo "→ Hitting ${BASE_URL}/api/providers/openrouter/models ..."

RESPONSE=$(curl -sS -X POST "${BASE_URL}/api/providers/openrouter/models" \
  -H "Content-Type: application/json" \
  -d "{\"api_key\": \"${OPENROUTER_API_KEY}\"}")

if ! echo "$RESPONSE" | python3 -c "import sys, json; json.load(sys.stdin)" >/dev/null 2>&1; then
  echo "ERROR: non-JSON response:" >&2
  echo "$RESPONSE" >&2
  exit 2
fi

COUNT=$(echo "$RESPONSE" | python3 -c "import sys, json; d=json.load(sys.stdin); print(len(d.get('models', [])))")
FIRST_FIVE=$(echo "$RESPONSE" | python3 -c "import sys, json; d=json.load(sys.stdin); [print('  -', m) for m in d.get('models', [])[:5]]")

if [[ "$COUNT" -eq 0 ]]; then
  echo "FAIL: model list is empty. Check API key and server logs." >&2
  echo "$RESPONSE" | head -c 500 >&2
  exit 3
fi

echo "✓ Discovery OK — ${COUNT} models available."
echo "First 5:"
echo "$FIRST_FIVE"
echo
echo "Next: pick one in Settings (e.g. anthropic/claude-3.5-sonnet) and run an extraction."
