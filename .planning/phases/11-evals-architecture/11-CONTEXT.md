# Phase 11: Evals Architecture - Context

**Gathered:** 2026-04-09
**Status:** Ready for planning

<domain>
## Phase Boundary

Set up Langfuse datasets, Python evaluation scripts, and Langchain evals in the admin container. Run offline evaluations (scoring production traces using LLM-as-a-judge for precision/recall and scripts for format adherence) without adding latency to the user extraction pipeline.
</domain>

<decisions>
## Implementation Decisions

### Evaluator Trigger Mechanism
- **D-01:** Cron/Batch Job (Recommended). The evaluator will run periodically in batches to fetch traces from Langfuse and score them, keeping the evaluation process decoupled from real-time user traffic.

### LLM-as-a-Judge Configuration
- **D-02:** Admin Bifrost Proxy (Recommended). The evaluator will connect to the existing LiteLLM proxy running in the admin compose stack (s2j-admin-bifrost) to isolate evaluation API traffic from the production user proxy.

### Admin Container Integration
- **D-03:** Dedicated Evaluator Container (Recommended). The evaluation script will run inside its own lightweight Python Docker container orchestrated alongside the rest of the services in `docker-compose.admin.yml`.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements & Roadmap
- `.planning/ROADMAP.md` — Phase 11 Goals and Success Criteria.
- `.planning/REQUIREMENTS.md` — Core platform requirements mapping.

### Technical Integration Points
- `infra/admin/docker-compose.admin.yml` — Where the new Dedicated Evaluator Container will be integrated.
- `scripts/seed_full_langfuse_dataset.py` — The ground truth dataset generation script establishing the baseline for evaluation.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `scripts/seed_full_langfuse_dataset.py` demonstrates how to initialize the `Langfuse` Python client and interact with the API correctly.

### Established Patterns
- Admin compose stack uses `argus-network`.
- Langfuse is exposed on port 3002 locally.
- Bifrost (Admin) is exposed on port 8081 locally.

### Integration Points
- The dedicated evaluator container must be added to `infra/admin/docker-compose.admin.yml`.
- The container needs a simple `Dockerfile` (e.g., `infra/admin/evaluator/Dockerfile`) and a `requirements.txt` containing `langfuse` and `langchain` packages.
</code_context>

<specifics>
## Specific Ideas

- The evaluation should focus on Comprehensiveness/Recall (did it miss deliverables?), Faithfulness/Precision (did it hallucinate?), and Format Adherence (does the output match the expected Jira schema).
</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.
</deferred>

---

*Phase: 11-evals-architecture*
*Context gathered: 2026-04-09*
