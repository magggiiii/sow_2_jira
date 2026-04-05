# SOW-to-Jira AI Guidelines

## Branching Strategy
- **CRITICAL RULE:** All active development, testing, and fixes MUST happen locally on the **`test`** branch.
- **Workflow Protocol:** 
  1. Test changes locally on `test`.
  2. Push to remote `test` branch.
  3. STOP and ask user for permission to merge/push to `dev`.
  4. Once `dev` is updated, STOP and ask user for permission to merge/push to `main`.
- NEVER push directly to `main` or skip permission steps.
