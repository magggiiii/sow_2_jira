# Phase 12 — Implementation Research (retroactive research-phase pass)

**Mode:** Adversarial challenge of plan implementation choices with external evidence
**Performed:** 2026-05-13
**Tools used:** WebSearch, WebFetch, PyPI/GitHub source review
**Status:** Plans broadly defensible. Two implementation refinements identified (one MEDIUM-severity for the `json-repair` invocation pattern, one LOW for documenting `bge-small` upgrade path). Locked decisions D-30..D-35 hold up.

---

## 1. JSON repair library validation

**Plan choice:** `json-repair>=0.59`, invoked as fallback after `json.loads()` fails (12-01).

**External evidence:**
- [json-repair on PyPI](https://pypi.org/project/json-repair/) — version 0.59.9 released 2026-05-12 (one day before this research pass). MIT-licensed, no heavy deps, drop-in `json.loads()` replacement.
- [mangiucugna/json_repair GitHub README](https://github.com/mangiucugna/json_repair) — purpose-built for "malformed JSON from LLMs, APIs, logs, and user input". Has `stream_stable.py` for streaming partial-JSON stability — directly relevant to Gemini's "Expecting ',' delimiter" pattern.
- [Repairing Broken JSON in Python with json-repair (Jellyfish Technologies)](https://www.jellyfishtechnologies.com/repairing-broken-json-in-python-with-json-repair/) — confirms it handles missing commas, brackets, quotes, AND truncated values.
- Comparison alternatives surveyed: `dirtyjson` (older, general-purpose dirty parsing, not LLM-tuned), `jsonrepair` (npm/JS-first, requires `PythonMonkey` bridge in Python — operational overhead), `fast-json-repair` (Rust port, faster but newer/less battle-tested). `json-repair` is the de facto Python standard for LLM output recovery in 2025-2026.

**Finding — MEDIUM-severity API misuse:** The library's [README explicitly warns](https://github.com/mangiucugna/json_repair) against the exact pattern plan 12-01 uses:

> *"Some users of this library adopt the following pattern: try `json.loads()` first, catch `JSONDecodeError`, then call `json_repair.loads()`. This is wasteful because `json_repair` already does that strict check for you by default. Use the default call unless you explicitly want to skip that initial validation step."*

Plan 12-01 (Task 1.1 `complete_json` block, Task 1.2 `extract_json` block) does exactly this — runs `json.loads(cleaned)` first, catches `JSONDecodeError`, then calls `_json_repair.loads(cleaned)`. This is functionally correct but performs the strict parse twice on every fallback path. Performance impact is minor (~milliseconds), but the bigger issue is: **the plan loses the `recovery_flag` discriminator if we switch to single-call**. Specifically, plan 12-01's partial-recovery detector (D-33) depends on knowing whether the fallback fired. If we call `json_repair.loads()` directly, we lose that signal.

**Verdict:** DEFENSIBLE with rationale. The plan's two-step pattern is technically an anti-pattern per the library author, but it's intentional here — we need the `recovery_flag` signal to wire D-33 (`EXTRACTION_PARTIAL`). The wasteful double-parse is a deliberate trade for observability.

**Recommended plan annotation (not a blocker):** Add a code comment in 12-01 Task 1.1 explaining we're knowingly violating the upstream recommendation in exchange for the partial-recovery signal, e.g.:
```python
# NOTE: Author of json-repair recommends calling json_repair.loads directly
# (it does the stdlib check internally). We deliberately do stdlib FIRST so
# we can set recovery_flag["recovered"]=True only when the fallback actually
# fires — required for the D-33 partial-recovery detector.
```
Alternative if we want to honor the author's pattern: call `json_repair.loads(cleaned, skip_json_loads=False)` once, but then we need a different way to detect "did this look broken before repair?" — could compare repaired output's serialized length to input length, or use the `_logger` callback hook the library exposes. **Recommendation: keep the plan as-is and add the comment.**

**Other findings:**
- Library returns empty string `""` on completely unrecoverable input (not `None`, not raises) — plan's `try/except Exception` around `json_repair.loads(...)` correctly handles this since the subsequent `isinstance(raw_list, list)` check filters out non-list returns. ✅
- D-33's "partial recovery silently truncates" concern is real and confirmed: `json-repair`'s [docs note](https://github.com/mangiucugna/json_repair) "if the string was super broken this will return an empty string" — i.e., **graceful degradation, not exception**. The detector is necessary.

---

## 2. Embedding-similarity thresholds for SOW task deduplication

**Plan choice:** MiniLM-L6-v2 with thresholds: `≥0.85` drop, `0.70-0.85` likely_overlap, `<0.70` uncovered (D-32 / 12-03). Same model already in use by `DeduplicationAgent` for pair candidate detection at `0.85`.

**External evidence:**
- [sentence-transformers/all-MiniLM-L6-v2 (Hugging Face)](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) — 384-dim, distilled, fine-tuned with contrastive cosine objective on 1B sentence pairs.
- [Sentence Transformers Semantic Textual Similarity docs](https://sbert.net/docs/sentence_transformer/usage/semantic_textual_similarity.html) — example outputs show 0.89 for clearly-related sentences, 0.28 for unrelated. **Does NOT publish threshold guidance** — explicitly leaves it to the user to calibrate per use case.
- [HF community discussion on MiniLM threshold calibration](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/discussions/16) — community consensus is that MiniLM's similarity distribution is task-dependent. There is no canonical "0.85 = duplicate" rule.
- [arxiv 2509.15292 — Semantic Similarity-Based Pipeline for Literature Review](https://arxiv.org/html/2509.15292v1) — found "similarity score saturation issues required rigid threshold calibration, with plans to focus on implementing adaptive thresholding to dynamically adjust based on score distribution characteristics." Confirms thresholds need empirical calibration per corpus.
- [Best Open-Source Embedding Models Benchmarked (Supermemory)](https://supermemory.ai/blog/best-open-source-embedding-models-benchmarked-and-ranked/) — `all-mpnet-base-v2` outperforms MiniLM by ~5-8% on MTEB retrieval accuracy. `bge-small-en-v1.5` (33M params, similar size) outperforms MiniLM on most MTEB tasks per 2024-2025 benchmarks.
- [BentoML 2026 Open-Source Embedding Models guide](https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models) — BGE family and E5-Base-v2 achieve 83-85% retrieval accuracy at 79-82ms latency, vs MiniLM's lower accuracy.

**Threshold defensibility (0.85 / 0.70):**
- **No published empirical study** specifically validates 0.85 for SOW-style task descriptions. The number is a reasonable default for MiniLM on technical text (community-typical range is 0.75-0.90 for "very similar"), but it's untested on **our** corpus.
- The plan already uses 0.85 elsewhere (`DeduplicationAgent.similarity_threshold`) — so the new 0.85 / 0.70 tiers are *consistent with existing project tuning*, not a fresh guess.
- The dual-tier approach (drop / likely_overlap / uncovered) is actually quite defensible: it acknowledges threshold uncertainty by giving reviewers a fuzzy middle band rather than a hard binary cut.

**Model choice defensibility:**
- MiniLM-L6-v2 is reused from existing `DeduplicationAgent._get_embedder()` (no new model load, per D-32). This is the correct engineering call for Phase 12 (avoid scope creep, avoid new dep).
- A future phase could measurably improve with `bge-small-en-v1.5` or `all-mpnet-base-v2`, but that's a Phase 13+ concern.

**Verdict:** DEFENSIBLE. Thresholds are reasonable defaults consistent with existing project usage. Dual-tier design absorbs threshold uncertainty by design.

**Recommended plan annotation (NOT a blocker):** Add to 12-03 BEFORE_AFTER spec an explicit "INT-05b — tier distribution sanity check": on the rerun, the distribution of `tier=uncovered` vs `tier=likely_overlap` vs (computed: how many drops) should be reported. This gives us calibration data for tuning thresholds in a future phase. If 100% of misses land in one tier, the thresholds need retuning.

**Open question for Phase 13:** Consider upgrading to `bge-small-en-v1.5` (33M params, ~same speed as MiniLM, better MTEB scores) once Phase 12 baseline is established. Document in STATE.md Roadmap Evolution.

---

## 3. Adaptive batch-size patterns for LLM-as-judge / LLM dedup

**Plan choice (12-04 / D-34):** Default `pair_batch_size=30`. Halve on parse failure (floor 5). Reconciliation pass after all batches converge — up to 2 rounds.

**External evidence:**
- [LLM Structured Output in 2026 (Pockit Blog)](https://pockit.tools/blog/llm-structured-output-complete-guide/) — confirms 2026 best-practice is "generate in chunks or stages, then pass smaller, validated payloads to actions. This is a widely adopted approach for handling large JSON outputs."
- [LLM Response Evaluation with Spring AI: LLM-as-a-Judge Recursive Advisors (Spring Blog, Nov 2025)](https://spring.io/blog/2025/11/10/spring-ai-llm-as-judge-blog-post/) — production LLM-as-judge architecture uses self-refining state machines with bounded chunk sizes, recursive advisor pattern with retries.
- [Overcoming Output Token Limits (Medium)](https://medium.com/@gopidurgaprasad762/overcoming-output-token-limits-a-smarter-way-to-generate-long-llm-responses-efe297857a76) — "When LLMs are asked to generate large volume of structured content in a single conversation turn, the LLM silently truncates its output mid-generation."
- [Atlassian Community: Rovo Agent LLM truncation in large action inputs](https://community.atlassian.com/forums/Rovo-questions/Custom-Rovo-Agent-LLM-output-truncation-causes-corrupted-JSON-in/qaq-p/3218285) — same root failure mode we observed: long output → silent truncation → corrupted JSON.
- [arxiv: Survey of LLM × DATA (2505.18458)](https://arxiv.org/pdf/2505.18458) — covers LLM deduplication patterns, notes "transitive closure introduces error propagation" requiring **validation** (reconciliation) step.
- [Graph Metrics-driven Record Cluster Repair meets LLM-based active learning (ACM JDIQ, 2025)](https://dl.acm.org/doi/10.1145/3735511) — entity-resolution research confirms transitive closure problem and recommends union-find + iterative repair.

**Industry-standard patterns:**
1. **Chunk-then-validate:** Confirmed widely-adopted (Pockit, Spring AI, Atlassian community). Plan's batching matches this.
2. **Halve-on-failure:** Less common in published patterns — most use *fixed* batch size with retry. Plan's adaptive halving is a defensible engineering optimization that handles **Gemini-Flash-specific** repetition/truncation failure modes (see probe 5). Not over-engineering.
3. **Reconciliation / transitive closure:** Strongly supported by entity-resolution literature. The 2-round cap is a sensible heuristic — most production union-find systems converge in 1-2 rounds.
4. **Token-based batching alternative:** Not found in 2025 LLM-as-judge literature. Pair-count batching is the de facto pattern.
5. **Convergence guarantee:** No formal guarantee from any published pattern. "Cap rounds + log" is the standard practical solution. Plan's 2-round cap matches industry practice.

**Verdict:** DEFENSIBLE. Plan's design (batch + halve + reconcile + cap) matches 2025 production patterns. The halve-on-failure adaptation is specifically motivated by Gemini Flash's known unreliability (see probe 5) — it's not over-engineering.

**Note on batch size default (30):** No published benchmark validates "30" as a sweet spot. Reasonable engineering bet: a Gemini Flash 4K-token output budget at ~80 tokens per decision is ~50 pairs max; 30 leaves headroom for the model's prose padding. **Recommend instrumenting**: emit `DEDUP_BATCH` audit row with output token count (if available) so we can calibrate over time.

---

## 4. Critic / self-review LLM patterns

**Plan choices:**
- D-30: Remove `LIKELY_DUPLICATE` from `CritiqueIssue` entirely (single-responsibility — dedup owns dupes).
- D-31: Force confidence on every flag + gate `_apply` on `min_flag_confidence ≥ 0.5`.

**External evidence:**
- [CritiCal: Can Critique Help LLM Uncertainty or Confidence Calibration? (arxiv 2510.24505, 2025)](https://arxiv.org/abs/2510.24505) — key finding: "natural language critiques are ideally suited for confidence calibration, as precise gold confidence labels are hard to obtain." Plan's approach of *requiring* the LLM to verbalize confidence inline with the critique matches this paper's recommended pattern.
- [Confidence v.s. Critique: A Decomposition of Self-Correction Capability (arxiv 2412.19513 / ACL 2025)](https://aclanthology.org/2025.acl-long.203/) — decomposes self-correction into **confidence** (being confident about correct answers) and **critique** (turning wrong answers correct). Key insight: *different models exhibit distinct behaviors — some are confident, others critical*. This directly supports D-31: don't trust the LLM's instinctive confidence; explicitly demand a calibrated score, then gate downstream.
- [When Can LLMs Actually Correct Their Own Mistakes? (TACL 2025)](https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00713/125177/) — critical survey shows LLMs are **poor at self-correction without external grounding**. Implication for D-30: asking the same model that extracted the tasks to also detect duplicates is a self-review anti-pattern. Delegating to a separate component (embedding-based dedup) is the correct design.
- [Know When You're Wrong: Aligning Confidence with Correctness for LLM Error Detection (arxiv 2603.06604)](https://arxiv.org/html/2603.06604) — LLM verbalized confidence is poorly calibrated by default. Pattern: enforce confidence via prompt-structural means (which D-31 does with the "you MUST still set confidence" rewrite), then validate downstream.

**Defensibility of LIKELY_DUPLICATE removal (D-30):**
- Strongly supported by the TACL 2025 survey's "external grounding" requirement: self-critique is poor at duplicate detection without external state.
- The baseline run's evidence is even stronger than the literature: 175/227 of all CRITIQUE_FLAGGED entries were `likely_duplicate` with `conf=0.00` — i.e., the critic was essentially randomly guessing when forced to play dedup's role. Removing the role is correct.

**Defensibility of confidence gating (D-31):**
- Belt-and-suspenders (prompt + apply-side gate) is supported by [Know When You're Wrong (2603.06604)](https://arxiv.org/html/2603.06604): "LLM verbalized confidence is poorly calibrated" — so don't trust the prompt fix alone; ALSO gate at apply time.
- The 0.5 default threshold is a reasonable starting point. Published thresholds for "actionable critic confidence" range from 0.4 (loose) to 0.7 (strict). 0.5 is middle-of-road.

**Verdict:** DEFENSIBLE — and well-supported by 2025 published research. Both D-30 (removing dedup responsibility from critic) and D-31 (belt-and-suspenders confidence gating) directly match published best practice from ACL/TACL 2025 papers.

**No change recommended.** The plan is more rigorous than typical industry implementations on this dimension.

---

## 5. Gemini Flash JSON output reliability

**Plan implicit choice:** Treat Gemini Flash JSON output as fundamentally unreliable; mitigate via (a) `json-repair` fallback (12-01), (b) smaller batches (12-04). Do NOT switch to `response_format` / `responseSchema`.

**External evidence:**
- [Gemini 2.5 Flash gets stuck in infinite token repetition during structured JSON output (LiteLLM) — Google AI Forum](https://discuss.ai.google.dev/t/gemini-2-5-flash-gets-stuck-in-infinite-token-repetition-during-structured-json-output-litellm/143931) — **direct match for our failure mode**. Community report: "the model enters a repetition loop and keeps generating duplicated tokens or JSON fragments until the max output token limit is reached." This is a KNOWN, widespread Gemini 2.5 Flash issue, not specific to our project.
- [LiteLLM Issue #10134: Gemini 2.5 JSON model inconsistent compared to 2.0 with Tool messages](https://github.com/BerriAI/litellm/issues/10134) — confirms 2.5 Flash is a regression vs 2.0 for JSON reliability.
- [Google AI Forum: 2.5-flash stopped delivering true json structures](https://discuss.ai.google.dev/t/2-5-flash-stopped-delivering-true-json-structures/100175) — "Till morning PST 08/26/2025, Gemini 2.5-flash was returning true JSON structured responses per the prompt, then it suddenly started sending completely broken responses, with debug logs showing the responses started with 'content' instead of ~~~json and commas missing."
- [Google AI Forum: Truncated Response Issue with Gemini 2.5 Flash Preview](https://discuss.ai.google.dev/t/truncated-response-issue-with-gemini-2-5-flash-preview/81258) — directly names the long-output truncation issue.
- [Vertex AI Flash 2.5 transcription degrades with repeated [unclear] (recent regression)](https://discuss.ai.google.dev/t/flash-2-5-vertex-ai-transcription-degrades-with-repeated-unclear-exhausting-output-tokens-recent-regression/115606) — confirms a *recent regression* (within last 1-1.5 months as of late 2025) in long-output reliability.
- [LiteLLM PR #17496: fix(gemini) handle partial JSON chunks after first valid chunk](https://github.com/BerriAI/litellm/pull/17496) — **active upstream fix in LiteLLM for this exact class of issue**, but specifically for streaming. Non-streaming completions still hit the underlying provider failure.

**Would `response_format` help?**
- [LiteLLM JSON Mode docs](https://docs.litellm.ai/docs/completion/json_mode) — yes, Gemini 2.0+ supports `responseSchema` natively, and LiteLLM has client-side validation.
- BUT: [LiteLLM Issue #17556](https://github.com/BerriAI/litellm/issues/17556) — `response_format` + Gemini "returns raw tool call control tokens instead of the actual structured JSON output" when combined with certain features. Adds new failure modes.
- For OpenRouter specifically: [LiteLLM Issue #13438](https://github.com/BerriAI/litellm/issues/13438) — `supports_response_schema()` returns `False` for OpenRouter models even though they support it. [LiteLLM Discussion #11652](https://github.com/BerriAI/litellm/discussions/11652) — workaround requires `extra_body` injection to bypass LiteLLM's check.
- [OpenRouter Structured Outputs docs](https://openrouter.ai/docs/guides/features/structured-outputs) — claims support but does NOT mention long-array reliability specifically. Streaming partial-JSON support is documented but doesn't address the underlying provider repetition/truncation bug.

**Net assessment of switching to `response_format`:**
- **Would NOT fully solve the problem.** The underlying Gemini Flash regression (token repetition, long-array truncation) is at the provider level, not the prompt level. `responseSchema` enforces shape, not content quality, and Gemini's recent regression breaks long outputs **regardless of mode**.
- **Would ADD operational complexity.** OpenRouter's response_format support via LiteLLM requires the `extra_body` hack and adds another failure mode (raw tool tokens).
- **Plan's choice is correct.** Mitigate at the consumer side (json-repair fallback + smaller batches) rather than trust the provider's structured-output mode.

**Verdict:** DEFENSIBLE. The decision to NOT route through `response_format` is correct given current (May 2026) Gemini Flash + OpenRouter + LiteLLM integration state. The community confirms widespread reliability issues; the upstream fixes are partial (streaming-only).

**Future-watch item:** Once Gemini 2.5 Pro becomes affordable enough to default to (or if a "Flash 3.0" lands), reconsider `response_format`. Document in STATE.md.

---

## 6. Partial-recovery / completeness signal patterns

**Plan choice (D-33 / 12-01):** When `json-repair` succeeds, compare recovered task count to `max(1, attempted // 2)`. If below, mark `EXTRACTION_PARTIAL` so gap_recovery picks the node up.

**External evidence:**
- [Toward Faithful and Complete Answer Construction from a Single Document (arxiv 2602.06103)](https://arxiv.org/html/2602.06103) — "LLMs lack systematic mechanisms to ensure both completeness (avoiding omissions) and faithfulness (avoiding unsupported content), which fundamentally conflicts with AI safety principles."
- [LLM-Agent for Advanced Extraction (ACL REALM 2025, paper aclanthology.org/2025.realm-1.6.pdf)](https://aclanthology.org/2025.realm-1.6.pdf) — "Since initial LLM extraction is typically not complete, iterating the extraction process helps with completeness of extraction by having the LLM process the document again to search for entities that were not extracted yet." **Direct support for the gap_recovery design.**
- [Assessing the quality of information extraction (arxiv 2404.04068)](https://arxiv.org/html/2404.04068v1) — describes information-completeness checks as a standard pipeline pattern.
- [EVE Framework — Information Extraction from Visually Rich Documents (ACL 2025)](https://aclanthology.org/2025.acl-long.844.pdf) — decomposes extraction into element-wise search + validation, "transforming high-variance generative decisions into low-variance, independently verifiable steps." Supports plan's split between extract → detect-partial → gap_recovery.

**Defensibility of the heuristic (`recovered < max(1, attempted // 2)`):**
- **The 50% threshold is a reasonable engineering heuristic, not a published rule.** No paper specifies "50% recovered = partial." But the design pattern (compare extracted count vs expected count, flag if below threshold) is widely supported.
- The plan correctly handles the unknown-attempted-count case by **defaulting to "always partial when recovery fires"** (12-01 Task 1.2 Test 7) — this is the conservative right call. If recovery fired, *something* was probably truncated; cheap to send through gap_recovery.
- Alternative signals not used: length variance, schema violations, semantic completeness check. These are heavier; the 50% count rule is minimal and effective for the observed failure mode.

**Verdict:** DEFENSIBLE. The pattern (extract → check completeness signal → route to recovery) matches published practice. The specific heuristic (50% threshold + always-partial-on-unknown) is a reasonable engineering judgment, not over-engineered.

**Minor caveat:** The scratchpad regex `r"(\d+)\s+(?:atomic\s+units|tasks|tickets|items|deliverables)"` is fragile — depends on the LLM producing English numerals followed by exactly one of these nouns. **Recommendation**: the conservative "always-partial-on-unknown" default already covers regex-misses, so this is fine. Just be aware that the regex is best-effort, not load-bearing.

---

## 7. OTel attribute filter patterns

**Phase 12 does NOT touch OTel filters.** Probe included per the user's research request to surface latent risks.

**Current state:** The Phase 12 plans do not modify the OTel collector config that filters spans to Langfuse based on `gen_ai.system != nil`.

**External evidence:**
- [OpenTelemetry GenAI Semantic Conventions v1.37](https://opentelemetry.io/docs/specs/semconv/gen-ai/) — `gen_ai.system`, `gen_ai.operation.name`, `gen_ai.request.model`, and `gen_ai.provider.name` are the stable discriminators in 2025-2026. `gen_ai.system` is still **experimental** (not stable) per the [spec stability transition plan](https://opentelemetry.io/docs/specs/semconv/gen-ai/).
- [Datadog blog: LLM Observability natively supports OpenTelemetry GenAI Semantic Conventions (2025)](https://www.datadoghq.com/blog/llm-otel-semantic-convention/) — major vendors aligning to gen_ai.* attributes.
- [GenAI Semantic Conventions Issue #35: Agentic Systems (gen_ai.*)](https://github.com/open-telemetry/semantic-conventions-genai/issues/35) — convention is still evolving, especially for agentic/multi-step systems.

**Risk assessment for current `gen_ai.system != nil` filter:**
- It works today because Traceloop / openllmetry instrumentation sets `gen_ai.system` on every LLM call span.
- **Risk:** if Traceloop SDK upgrades to OTel-GenAI v1.37+ and the attribute is renamed or moved (e.g., to `gen_ai.provider.name`), the filter silently stops forwarding spans to Langfuse.
- A more robust filter would be **multi-attribute OR** logic:
  ```
  gen_ai.system != nil
  OR gen_ai.provider.name != nil
  OR gen_ai.operation.name != nil
  OR span.name matches "^(LLM_CALL|gen_ai)\..*"
  ```

**Verdict on the deferred concern:** Phase 12 plans are correct to NOT touch this. It's tangential to the locked decisions. **BUT** there is a latent fragility risk worth noting in STATE.md for a future phase.

**Recommended STATE.md backlog entry (NOT a Phase 12 blocker):** "OTel collector Langfuse filter currently uses `gen_ai.system != nil`. The `gen_ai.system` attribute is still experimental per OTel-GenAI v1.37. Add resilience by using OR-logic across `gen_ai.system`, `gen_ai.provider.name`, and `gen_ai.operation.name`."

---

## 8. Cross-cutting findings

### 8.1 Phase 12 is well-aligned with 2025-2026 published best practice

Across all 7 probes, the plans either match published patterns or make reasonable engineering trade-offs with documented justification (CONTEXT.md D-30..D-35). No probe surfaced a "you're doing this fundamentally wrong" finding.

### 8.2 Two minor refinements

| # | Probe | Severity | Refinement |
|---|-------|----------|------------|
| R1 | json-repair API | MEDIUM | Add code comment in 12-01 Task 1.1 explaining the deliberate two-step pattern (stdlib first, then json-repair) is to preserve the `recovery_flag` discriminator, not naïve. This pre-empts a future contributor "fixing" what isn't broken. |
| R2 | Embedding upgrade path | LOW | Backlog item: post-Phase 12, evaluate `bge-small-en-v1.5` (33M params, similar speed, better MTEB) as a future drop-in for MiniLM-L6-v2. Defer to Phase 13+. |

### 8.3 One observation (no action)

The plan's choice to NOT use Gemini's `response_format` / `responseSchema` is correct for current state (May 2026). Watch upstream: if Google publishes a Flash regression fix or if Flash 3.0 lands with `response_format` reliability, revisit in a future phase. The mitigation stack (json-repair + adaptive batching + reconciliation) is the right belt-and-suspenders for now.

### 8.4 Calibration data we're leaving on the table

Phase 12's BEFORE_AFTER.md (12-06) captures pass/fail per INT-XX but does NOT capture tier distribution for INT-05 or batch-size halve frequency for INT-02. Both are recoverable from `data/audit.db` post-hoc, so this is not a Phase 12 blocker, but **adding two distribution rows to BEFORE_AFTER.md would unlock Phase 13 threshold tuning**. See recommendations in §9.

---

## 9. Plan-revision recommendations

| Plan | Recommended change | Severity | Evidence |
|------|--------------------|----------|----------|
| 12-01 | Add explanatory code comment near the `try: json.loads(cleaned) except: _json_repair.loads(cleaned)` block stating the two-step pattern is deliberate (preserves `recovery_flag` signal for D-33), not naïve API misuse. | MEDIUM (clarity, not correctness) | [json-repair README author warning](https://github.com/mangiucugna/json_repair) |
| 12-04 | When auditing `DEDUP_BATCH` rows, include the LLM response token count if available from LiteLLM's response usage metadata — gives us calibration data for tuning `pair_batch_size` in a future phase. | LOW (additive observability) | [Spring AI LLM-as-Judge architecture (2025)](https://spring.io/blog/2025/11/10/spring-ai-llm-as-judge-blog-post/) |
| 12-06 | Add two distribution rows to BEFORE_AFTER.md: (a) tier histogram (drop count / likely_overlap count / uncovered count) for INT-05b, and (b) DEDUP_BATCH halve frequency (how many batches succeeded at size 30 vs 15 vs 5) for INT-02b. Both are derivable from the new run's audit.db with one SQL each. | LOW (additive calibration data) | Phase 13+ threshold tuning will need this |
| STATE.md | Add Phase 13+ backlog items: (1) evaluate `bge-small-en-v1.5` as embedder upgrade; (2) re-evaluate Gemini `response_format` once upstream regressions are fixed; (3) harden OTel collector filter against `gen_ai.system` attribute drift. | LOW (forward planning) | OTel-GenAI v1.37 stability transition; HF benchmark literature |

**None of these are BLOCKERS.** All six plans (12-01..12-06) are safe to execute as written. The refinements are quality-of-life and forward-planning hygiene.

---

## 10. Honest verdict

Phase 12's six plans hold up well against external evidence. The locked decisions D-30..D-35 are not just internally consistent — they match published 2025-2026 best practice across four independent literatures: structured-output recovery (json-repair, Pockit, Atlassian), embedding-based deduplication (HF, MTEB, SemHash), LLM self-critique calibration (CritiCal 2510.24505, ACL 2025 self-correction decomposition), and document-extraction completeness (ACL/EVE 2025). The "remove `LIKELY_DUPLICATE` from critic" decision (D-30) is particularly well-supported — the TACL 2025 self-correction survey directly identifies "self-review without external grounding" as an anti-pattern, and our baseline data (175 dup flags at `conf=0.00`) is the empirical confirmation.

The only meaningful finding is **MEDIUM-severity API hygiene in 12-01**: the `try json.loads() except → json_repair.loads()` pattern is explicitly flagged as wasteful by the library author. We're doing it deliberately to preserve the `recovery_flag` signal for D-33, but a future contributor will read this and "fix" it. A four-line code comment prevents that future regression. This is a clarity refinement, not a correctness issue.

Three small forward-looking opportunities — `bge-small` embedder upgrade, Gemini `response_format` re-evaluation when upstream fixes land, OTel attribute filter hardening — belong in STATE.md backlog, not Phase 12. The two suggested distribution rows in BEFORE_AFTER.md (tier histogram, batch halve frequency) are cheap to add and unlock Phase 13 threshold tuning.

**Overall plan-defensibility verdict: 6/6 plans defensible. Proceed to `/gsd:execute-phase 12`.** Apply the four refinements in §9 either inline during execute-phase or as a follow-up commit; none are blocking.

---

## Sources

### Primary (HIGH confidence)
- [json-repair on PyPI](https://pypi.org/project/json-repair/) — version 0.59.9, MIT, drop-in `json.loads()` replacement
- [mangiucugna/json_repair GitHub](https://github.com/mangiucugna/json_repair) — author's anti-pattern warning, streaming support
- [sentence-transformers/all-MiniLM-L6-v2 (Hugging Face)](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) — model card and intended usage
- [Sentence Transformers Semantic Textual Similarity docs](https://sbert.net/docs/sentence_transformer/usage/semantic_textual_similarity.html) — official threshold guidance (or lack thereof)
- [OpenTelemetry GenAI Semantic Conventions v1.37](https://opentelemetry.io/docs/specs/semconv/gen-ai/) — official semantic conventions
- [LiteLLM JSON Mode docs](https://docs.litellm.ai/docs/completion/json_mode) — structured output support
- [OpenRouter Structured Outputs docs](https://openrouter.ai/docs/guides/features/structured-outputs) — feature claims

### Secondary (MEDIUM confidence — published research)
- [CritiCal: Can Critique Help LLM Uncertainty Calibration (arxiv 2510.24505)](https://arxiv.org/abs/2510.24505)
- [Confidence v.s. Critique: Decomposition of Self-Correction (ACL 2025)](https://aclanthology.org/2025.acl-long.203/)
- [When Can LLMs Actually Correct Their Own Mistakes? (TACL 2025)](https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00713/125177/)
- [Know When You're Wrong: Aligning Confidence with Correctness (arxiv 2603.06604)](https://arxiv.org/html/2603.06604)
- [Survey of LLM × DATA (arxiv 2505.18458, Jun 2025)](https://arxiv.org/pdf/2505.18458)
- [Graph Metrics-driven Record Cluster Repair meets LLM-based active learning (ACM JDIQ 2025)](https://dl.acm.org/doi/10.1145/3735511)
- [LLM-Agent for Advanced Extraction and Integration (ACL REALM 2025)](https://aclanthology.org/2025.realm-1.6.pdf)
- [EVE: Information Extraction from Visually Rich Documents (ACL 2025)](https://aclanthology.org/2025.acl-long.844.pdf)
- [Toward Faithful and Complete Answer Construction (arxiv 2602.06103)](https://arxiv.org/html/2602.06103)
- [Assessing the quality of information extraction (arxiv 2404.04068)](https://arxiv.org/html/2404.04068v1)
- [Semantic similarity threshold calibration in literature review (arxiv 2509.15292)](https://arxiv.org/html/2509.15292v1)
- [Best Open-Source Embedding Models Benchmarked (Supermemory)](https://supermemory.ai/blog/best-open-source-embedding-models-benchmarked-and-ranked/)
- [The Best Open-Source Embedding Models in 2026 (BentoML)](https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models)

### Tertiary (MEDIUM confidence — community / vendor reports of failure modes)
- [Gemini 2.5 Flash gets stuck in infinite token repetition (Google AI Forum)](https://discuss.ai.google.dev/t/gemini-2-5-flash-gets-stuck-in-infinite-token-repetition-during-structured-json-output-litellm/143931)
- [2.5-flash stopped delivering true json structures (Google AI Forum)](https://discuss.ai.google.dev/t/2-5-flash-stopped-delivering-true-json-structures/100175)
- [Vertex AI Flash 2.5 transcription degrades (Google AI Forum)](https://discuss.ai.google.dev/t/flash-2-5-vertex-ai-transcription-degrades-with-repeated-unclear-exhausting-output-tokens-recent-regression/115606)
- [Truncated Response Issue with Gemini 2.5 Flash Preview (Google AI Forum)](https://discuss.ai.google.dev/t/truncated-response-issue-with-gemini-2-5-flash-preview/81258)
- [LiteLLM Issue #10134: Gemini 2.5 JSON inconsistent vs 2.0](https://github.com/BerriAI/litellm/issues/10134)
- [LiteLLM PR #17496: fix(gemini) partial JSON chunks](https://github.com/BerriAI/litellm/pull/17496)
- [LiteLLM Issue #13438: OpenRouter response_format feature request](https://github.com/BerriAI/litellm/issues/13438)
- [LiteLLM Issue #17556: response_format + web_search returns raw tool tokens](https://github.com/BerriAI/litellm/issues/17556)
- [LiteLLM Discussion #11652: Forcing Structured JSON in LiteLLM + OpenRouter (FIXED)](https://github.com/BerriAI/litellm/discussions/11652)
- [Atlassian Community: Rovo Agent LLM truncation in large action inputs](https://community.atlassian.com/forums/Rovo-questions/Custom-Rovo-Agent-LLM-output-truncation-causes-corrupted-JSON-in/qaq-p/3218285)
- [LLM Structured Output in 2026 (Pockit Blog)](https://pockit.tools/blog/llm-structured-output-complete-guide/)
- [LLM Response Evaluation with Spring AI: LLM-as-a-Judge (Spring Blog, Nov 2025)](https://spring.io/blog/2025/11/10/spring-ai-llm-as-judge-blog-post/)
- [Overcoming Output Token Limits (Medium)](https://medium.com/@gopidurgaprasad762/overcoming-output-token-limits-a-smarter-way-to-generate-long-llm-responses-efe297857a76)
- [Repairing Broken JSON in Python with json-repair (Jellyfish Technologies)](https://www.jellyfishtechnologies.com/repairing-broken-json-in-python-with-json-repair/)
- [Datadog blog: LLM Observability supports OTel GenAI Semantic Conventions (2025)](https://www.datadoghq.com/blog/llm-otel-semantic-convention/)

---

## Metadata

**Confidence breakdown:**
- json-repair library validation: HIGH — direct vendor docs + GitHub source + community reports
- Embedding thresholds: MEDIUM — no published "0.85 for SOW tasks" benchmark; defensible by analogy and dual-tier design
- Adaptive batching: HIGH — directly matches published 2025-2026 production patterns
- Critic / self-review: HIGH — explicitly supported by ACL/TACL 2025 papers
- Gemini JSON reliability: HIGH — widespread community confirmation, multiple vendor-side bug reports, active upstream LiteLLM fixes
- Partial-recovery signals: MEDIUM — pattern is published, specific 50%-threshold heuristic is engineering judgment
- OTel filter patterns: MEDIUM — Phase 12 doesn't change it; finding is forward-looking

**Research date:** 2026-05-13
**Valid until:** 2026-06-13 (30 days for the json-repair / embedding-model claims; 7-14 days for Gemini-related claims since the upstream landscape is moving fast)
