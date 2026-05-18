# v2 Learning-Agent Evolution — Theoretical Research (post-Phase 12)

**Mode:** Forward-looking research, NOT a phase commitment
**Horizon:** 3-6 months out (revisit ~ Aug-Nov 2026)
**Trigger to revisit:** After Phase 12 ships AND we have ≥20 production runs accumulated in `data/audit.db` AND Phase 11 eval harness is producing per-agent scores from a stable golden dataset
**Status:** Research artifact — no implementation should follow without explicit `/gsd:new-milestone` or `/gsd:add-phase` triggered by the user
**Author:** Research probe via GSD project researcher
**Confidence overall:** MEDIUM-HIGH on ecosystem facts (multiple sources, official docs); MEDIUM on integration sketches (extrapolation from our code); LOW on cost / ROI projections (no production data yet)

---

## 1. The system this is FOR (target state)

Today, post-Phase 12, the SOW-to-Jira pipeline is **a stateless, per-document extraction engine** with a six-stage agent chain:

```
PageIndex tree
  → SectionClassifier  (pipeline/agents/classifier.py)        gates extraction
  → TaskExtractionAgent (pipeline/agents/extraction.py)       few-shot + scratchpad
  → TaskStateAgent     (pipeline/agents/state.py)             deterministic, no LLM
  → TaskCritic         (pipeline/agents/critic.py)            self-critique (auto-fix + flag)
  → CoverageChecker    (pipeline/agents/coverage_check.py)    "what was missed?"
  → CoverageTracker.mark_covered                              deterministic
  ... per-run dedup pass:
  → DeduplicationAgent (pipeline/agents/deduplication.py)     MiniLM NN + LLM pair-vote
  → GapRecoveryAgent   (pipeline/agents/gap_recovery.py)      uncovered-node retry
  → JiraClient.push_tasks (integrations/jira_client.py)
```

Prompts are constants in source (`EXTRACTION_SYSTEM_PROMPT`, `CRITIC_SYSTEM_PROMPT`, etc.). Improvement requires a PR. Each run is independent; the only cross-run state is `data/project_indices/<project_key>/index.npz` (Wave 2D's per-project embedding store, used as a dedup signal *only*).

The target end-state ("Operational Ally") flips five axes:

| Axis | Today | Target |
|------|-------|--------|
| **Memory** | Stateless per run; per-project embeddings used only for dedup matches | Per-customer + per-sector memory shaping classification, extraction, and advisory output |
| **Specialization** | One prompt, one taxonomy across fintech/healthcare/SaaS/govtech | Sector-specific prompts/exemplars/risk-pattern libraries, swapped at runtime |
| **Feedback** | `data/audit.db` accumulates but loops back nowhere; UI edits aren't captured | Human edits/rejects/Jira-close outcomes become labeled signal that updates prompts (or weights) |
| **Posture** | Reactive: extract on upload | Advisory: "this SOW is integration-heavy, 3 similar past SOWs missed estimating a senior backend lead" |
| **Improvement loop** | Manual prompt edits via PR | Offline batch optimization on accumulated audit data; A/B routing; optional fine-tune for highest-volume sectors |

The architectural shifts implied are large but factor cleanly:

1. **Memory store** — sector-tagged + customer-tagged vector index; structured "risk pattern" library (probably a small relational table, not a graph); cold-start fallback.
2. **Feedback signal capture** — UI must capture which fields were edited, whether the task was pushed, and (longer feedback loop) whether the Jira issue resolved as done vs cancelled. This is the single most expensive missing piece.
3. **Optimization target** — start with prompt + few-shot exemplar optimization; only escalate to weight-level RL/SFT for one or two agents where prompt-space is saturated.
4. **Specialization** — RAG-style sector exemplar retrieval over fine-tuning; LoRA-per-sector is over-engineered for current volume.
5. **Online vs offline** — **offline-batch** (nightly or weekly job in the admin container); no online learning until we have monitoring tight enough to detect drift in real time.

Everything below maps recommendations back to *these six agents* and *these five axes*.

---

## 2. Microsoft agent-lightning — what it actually is

### Facts (HIGH confidence — multiple sources cross-verified)

| Fact | Source |
|------|--------|
| Repository | https://github.com/microsoft/agent-lightning |
| License | MIT |
| Stars | ~17.2k (as of fetch) |
| Latest release | v0.3.0, Dec 24, 2025 |
| Total commits on main | 255 |
| Total releases | 7 |
| Original arXiv paper | [2508.03680 — "Agent Lightning: Train ANY AI Agents with Reinforcement Learning"](https://arxiv.org/abs/2508.03680), Aug 5, 2025 |
| Public announcement | Oct 29, 2025 Microsoft Research blog post |

### Core thesis (HIGH confidence)

"Add reinforcement learning to AI agents **with virtually no code modification**." The framework's bet is that the agent-runtime and the training-loop should be physically separated, communicating only through a structured event log ("LightningStore"), and that this decoupling lets any existing agent (LangChain / OpenAI Agents SDK / AutoGen / CrewAI / custom Python) become RL-trainable.

### Core primitives (HIGH confidence — from arXiv + blog + README)

| Primitive | Role |
|-----------|------|
| **Agent** | Runs unmodified; emits structured events via `agl.emit_xxx()` helpers or auto-tracer |
| **LightningStore** | Central event repository — receives spans (prompts, tool calls, rewards) from agent runtime |
| **Algorithm** | Reads spans from LightningStore, computes updates (refined prompt OR new policy weights), posts back as "resources" |
| **Trainer** | Orchestrates the loop |
| **Resource** | The updatable artifact — a prompt template, a model adapter, or full weights |
| **LightningRL** | The framework's hierarchical RL algorithm with credit assignment for multi-step agents |

The "decouple training from agent code" claim is concrete: the agent runtime can run on CPU pods; the algorithm component runs on GPU pods; they only exchange typed events via LightningStore. **This is genuinely architecturally distinct from DSPy/TextGrad**, which run optimization in-process with the agent.

### Supported optimization (HIGH confidence)

- **RL** (primary focus): LightningRL + integrations with VERL, GRPO, PPO
- **Automatic prompt optimization** (recently added — see release notes)
- **Supervised fine-tuning** (SFT)
- Multi-agent: "selectively optimize one or more agents in a multi-agent system" — credit assignment is the marketed differentiator

### Infrastructure required (HIGH confidence)

- Agent runner: CPU-only is fine
- Algorithm (especially RL): **GPU required** for weight updates; the demoed deployments use 16-128 GPUs (e.g., Youtu-Agent)
- For *prompt-only* optimization, GPU footprint is much smaller (still needs to run forward passes for grading)
- Docker-friendly; Python 81.9% of codebase

### Maturity verdict (MEDIUM-HIGH confidence — this is the load-bearing assessment)

**Beta, evolving fast, not production-validated outside Microsoft demos.** Evidence:
- 17.2k stars in ~7 months → strong community interest, but star count is a vanity metric.
- Three documented case studies in the paper (text-to-SQL, RAG, math QA) — all *academic benchmark* tasks, not production deployments.
- Community projects listed (DeepWerewolf, Youtu-Agent, AgentFlow) are research demonstrators, not commercial.
- No public statement of production-readiness in either README or MS Research blog post.
- 0.x version number (0.3.0); 7 releases in 7 months suggests API instability.
- The framework's own positioning (paper title, blog tagline) is research-forward.

**Honest read:** This is research-quality code rapidly maturing toward production, with Microsoft's stewardship as a stability anchor. It is **not yet** what DSPy is (mature, production-deployed at JetBlue/Databricks/Replit/Walmart). Building on agent-lightning today means building on **shifting ground**; pinning a version and accepting a non-trivial migration cost in 6-12 months is the realistic posture.

Sources:
- [GitHub: microsoft/agent-lightning](https://github.com/microsoft/agent-lightning)
- [arXiv:2508.03680 — Agent Lightning: Train ANY AI Agents with RL](https://arxiv.org/abs/2508.03680)
- [Microsoft Research blog: Agent Lightning announcement](https://www.microsoft.com/en-us/research/blog/agent-lightning-adding-reinforcement-learning-to-ai-agents-without-code-rewrites/)

---

## 3. Per-agent integration sketch (how it'd apply to OUR 6 agents)

For each of our six agents: what would the agent-lightning "agent / environment / reward" look like, and what is the smallest unit of learning that could move a metric.

### 3.1 SectionClassifier (`pipeline/agents/classifier.py`)

**Output:** decision per node — `actionable` / `skip(legal|definitions|signature|context)` with confidence.

**Natural reward signal:** *Downstream-utility* — was extraction on a classified-actionable section productive (i.e., did it produce ≥1 task that survived dedup and was pushed)? Was a classified-skip section confirmed-skip (i.e., gap_recovery didn't have to recover from it)?

**Reward formula sketch:**
```
reward(classifier_decision) =
   if decision == "actionable":
      +1 per pushed task from this section, -0.5 per task rejected by human
   if decision == "skip":
      +1 if no task was later recovered from this section by gap_recovery
      -2 if gap_recovery had to pick this section up
```

**Smallest learning unit:** Prompt optimization (few-shot exemplars + classifier instruction). This is text-classification with a small label space; DSPy MIPROv2 or GEPA should reach ceiling with <100 labeled sections.

**agent-lightning fit:** Overkill. The agent is one LLM call per node; reward is sparse but cheap to compute from `data/audit.db` joined with `pipeline_output.json`. **Use DSPy here.**

### 3.2 TaskExtractionAgent (`pipeline/agents/extraction.py`)

**Output:** list of `RawTask` per node, each with title / description / acceptance criteria / dependencies / confidence.

**Natural reward signal (per task, downstream):**
```
keep_rate = (tasks kept unchanged in UI) / (tasks produced)
edit_rate = (tasks where title or AC was edited) / (tasks produced)
reject_rate = (tasks deleted before push) / (tasks produced)
duplicate_rate = (tasks merged in dedup) / (tasks produced)
gap_rate = (tasks recovered by gap_recovery on this same section) / (tasks produced + recovered)

reward ≈ keep_rate − 0.5 * edit_rate − 1.0 * reject_rate − 0.5 * duplicate_rate − 0.3 * gap_rate
```

This is **the** highest-value agent to optimize because (a) every other agent's input quality depends on its output, and (b) the audit log already captures most of the signals (D-33 from Phase 12 adds `EXTRACTION_PARTIAL`, dedup and critic already audit-log decisions per task).

**Smallest learning unit:** Few-shot exemplar pool optimization first (DSPy `BootstrapFewShotWithRandomSearch`), then instruction tuning (MIPROv2), then optional GEPA reflective evolution. **Only escalate to RL/SFT** if those plateau AND we have ≥1000 labeled traces per sector.

**agent-lightning fit:** Plausible if we go RL-fine-tune route after prompt-space is exhausted. The credit assignment ("which sub-step of extraction earned the keep-vs-reject?") is the marketed differentiator. But for the first 6 months post-launch, **DSPy/GEPA on prompts beats RL on weights** for cost/reliability — confirmed by the GEPA paper itself, which showed reflective prompt evolution beating GRPO by 6-20% with 35× fewer rollouts ([arXiv:2507.19457](https://arxiv.org/abs/2507.19457)).

### 3.3 TaskCritic (`pipeline/agents/critic.py`)

**Output:** for each task — `auto_fix(title|ac)` with new value+confidence, OR `flag(too_broad|vague_title|...)` with confidence (LIKELY_DUPLICATE was removed by Phase 12 D-30).

**Natural reward signal:**
- For auto-fixes: did human revert the fix (-1) or keep it (+1)?
- For flags: did the flag survive review (human agreed) (+1) or was it ignored/cleared (-0.5)?

**Smallest learning unit:** Two-stage prompt optimization — first the auto-fix prompt, then the flag-issue prompt. Use DSPy with two separate signatures and optimize jointly.

**agent-lightning fit:** Marginal. The critic is fundamentally a *classification + rewrite* problem; prompt-space optimization is the right tool. Reserve agent-lightning for if/when we want a learned reward model that predicts "will a human revert this fix?" trained on accumulated history.

### 3.4 CoverageChecker (`pipeline/agents/coverage_check.py`)

**Output:** list of missed_items per section.

**Natural reward signal (post Phase 12 D-32 tiering):**
- For each missed_item that the post-dedup tiering put in `uncovered`: did gap_recovery actually find it (+1) or was it a false alarm (-1)?
- For `likely_overlap` tier: did human review confirm overlap (+0.5) or surface a real gap (-1)?

**Smallest learning unit:** Prompt + threshold optimization. The tiering thresholds (0.85 / 0.70) are themselves hyperparameters that could be optimized end-to-end against the reward.

**agent-lightning fit:** Low. This is a precision/recall tradeoff problem and standard prompt optimization handles it cleanly.

### 3.5 DeduplicationAgent (`pipeline/agents/deduplication.py`)

**Output:** merge decisions on candidate pairs.

**Natural reward signal:**
- For merged pairs: did human un-merge (-2) or keep merge (+1)?
- For non-merged pairs above similarity threshold: did human manually merge later (-1)?
- For false-merge across sections in the same hierarchy: -2 (worst error class — semantically wrong merges).

**Smallest learning unit:** Prompt optimization on the pair-vote prompt + threshold tuning on the MiniLM cosine cutoff. Critical second order: D-34 adaptive batching threshold and reconciliation round count are also tunable.

**agent-lightning fit:** Low. Pair-decision is a binary classification task; DSPy handles it; the substrate is already audit-log-rich.

### 3.6 GapRecoveryAgent (`pipeline/agents/gap_recovery.py`)

**Output:** new tasks recovered from uncovered sections.

**Natural reward signal:** were recovered tasks kept by human (+1 each), or all rejected (-1 each)?

**Smallest learning unit:** Prompt optimization. Same shape as extraction but with a different priming context.

**agent-lightning fit:** Low. Mirror of extraction; same recommendation.

### 3.7 New "advisory" agent (NOT in current pipeline)

This is the agent the end-state demands but we don't yet have: post-pipeline, given the final `ManagedTask` corpus + sector + customer history, generate top-3 risks, recommended team shape, comparable past SOWs, and effort confidence interval.

**Natural reward signal:** Eventually, did the project ship on time / on budget? But that's a **very long feedback loop** (months). Bootstrap with: did human accept/edit the advisory copy? Did they read it (UI telemetry)?

**Smallest learning unit:** RAG over per-customer history + DSPy ChainOfThought signature. Don't ship this until everything below is in place.

### Cross-agent summary

| Agent | Best framework | Reasoning |
|-------|---------------|-----------|
| Classifier | DSPy (MIPROv2 / GEPA) | Small label space, prompt-space saturates fast |
| Extraction | DSPy (BootstrapFewShot → MIPROv2 → GEPA); RL escalation only if prompts saturate | Highest leverage; audit-log signal dense; GEPA paper shows it beats GRPO at this scale |
| Critic | DSPy | Classification + rewrite; prompt-tunable |
| Coverage | DSPy + threshold tuning | Precision/recall problem |
| Dedup | DSPy + threshold tuning | Binary pair classification |
| Gap recovery | DSPy | Mirror of extraction |
| (Future) Advisory | DSPy + RAG | New agent; needs cold-start design |

**Net read:** agent-lightning is **not the right primary substrate** for our 6 agents. The job is prompt-space optimization on small, well-defined LLM calls — that's DSPy's sweet spot. agent-lightning's RL machinery is the right hammer for problems where prompt-space is saturated and weight updates are required; we are 12-18 months from being plausibly in that regime.

---

## 4. Alternatives landscape (2025-2026)

### Comparison table

| Framework | Maturity (2026) | License | Compute | Online/Offline | Multi-agent | LiteLLM integration | Vendor lock-in |
|-----------|-----------------|---------|---------|----------------|-------------|---------------------|----------------|
| **DSPy** | Production (JetBlue, Databricks, Replit, Walmart) | MIT (Stanford NLP) | CPU+small GPU | Offline (compile-time) | Yes (modular) | First-class via `dspy.LM` | Low |
| **GEPA (in DSPy)** | Recent (ICLR 2026 Oral); production-ready as a DSPy optimizer | MIT | CPU+API calls | Offline | Yes | Via DSPy | Low |
| **TextGrad** | Published in Nature (2025); academic/early-production | MIT (zou-group) | CPU+API calls | Both (test-time refinement is its niche) | Limited | Yes | Low |
| **OPRO** | Research; no actively maintained library | Google paper | CPU+API | Offline | No | N/A | N/A |
| **MIPROv2** | Production (as a DSPy optimizer) | MIT | CPU+API | Offline | Yes | Via DSPy | Low |
| **agent-lightning** | Beta/v0.3 (Dec 2025); demos only | MIT | GPU for RL; CPU for prompt-opt | Offline | Yes (its differentiator) | Indirect (via VERL) | Medium (Microsoft) |
| **BAML** | Production (Boundary ML, commercial) | Apache 2.0 + commercial tier | CPU | N/A (no optimizer) | N/A | Yes | Low-Medium |
| **LangSmith/Hub** | Production | Commercial (LangChain) | N/A | N/A | N/A | Yes | High (LangChain ecosystem) |
| **OpenAI RFT** | GA (Apr 2025), o4-mini only | Closed | OpenAI hosted | Offline | No (per-model) | No (OpenAI-only) | **High** |
| **Plain RAG sector corpora** | N/A | Free | CPU | Online | Trivially | Yes | None |
| **Manual prompt updates from audit log** | N/A | Free | None | Offline | Yes | Yes | None |

### One-paragraph summaries

**DSPy (Stanford NLP)** — declarative LLM programs with automatic prompt + few-shot optimization. Mature, production-validated, decoupled from LiteLLM as of v3.2 so model swaps are clean. **Our top pick** for first-stage adoption. [Docs](https://dspy.ai/), [GitHub](https://github.com/stanfordnlp/dspy)

**GEPA** — newest DSPy optimizer (paper July 2025, ICLR 2026 Oral). Reflective prompt evolution that beat GRPO by 6-20% on six tasks with 35× fewer rollouts. Beats MIPROv2 by ~10%. Available today as `dspy.GEPA`. **Best in-class prompt optimizer.** [Paper](https://arxiv.org/abs/2507.19457), [DSPy GEPA docs](https://dspy.ai/api/optimizers/GEPA/overview/)

**TextGrad** — "autograd for text"; backpropagates LLM feedback through compound pipelines, PyTorch-style. Best at *test-time refinement of individual hard outputs*, complements DSPy nicely (DSPy at compile time, TextGrad at inference for hard cases). Published in Nature 2025. [GitHub](https://github.com/zou-group/textgrad)

**OPRO** — original "LLM optimizes its own prompts" paper from Google. No production-maintained library; the technique is absorbed into DSPy/GEPA. Skip.

**MIPROv2** — multi-prompt instruction proposal optimizer, ships in DSPy. Reliable, well-documented, production-deployed. GEPA is its modern successor for most tasks but MIPROv2 is the safer first-try because it's older and more stable. [Paper](https://arxiv.org/abs/2406.11695)

**BAML (Boundary ML)** — strongly typed LLM function definitions; *not an optimizer*, a production pattern. Solves the "schema drift between prompt and Pydantic" problem we already solve manually. Adjacent, complements DSPy, not a substitute.

**LangSmith / LangChain Hub** — observability + prompt versioning. We already have Langfuse Cloud via Phase 7. Skip — duplicative.

**OpenAI Reinforcement Fine-Tuning (RFT)** — GA April 2025 on o4-mini only. $100/hr compute, capped at $5000/job, billed for grading tokens too. Gives you a fine-tuned o4-mini that knows your domain. **Powerful but vendor-locked**: switches our LLM-provider-pluggable architecture into OpenAI-only for the sectors that use it. Reserve as an option for the one or two highest-volume sectors **only after** prompt-space is exhausted. [Pricing](https://help.openai.com/en/articles/11323177-billing-guide-for-the-reinforcement-fine-tuning-api), [Docs](https://platform.openai.com/docs/guides/reinforcement-fine-tuning)

**Plain RAG with sector corpora** — embed past extracted-and-approved SOWs by sector; retrieve top-K as few-shot exemplars at extraction time. No learning loop; just better context retrieval. **This is the cheapest concrete capability gain on the table** and we should ship it independent of whether we adopt any optimizer.

**Manual prompt updates from audit log** — discipline, not framework. Read audit log weekly, edit prompts, ship PR. **This is what we're doing today**. The honest reframing of the question this research answers is: "is the manual-prompt-update loop the right ceiling, or should we automate it?"

---

## 5. Architectural prerequisites (framework-independent)

Regardless of which optimizer wins, these foundations are required first. **None should be deferred until we pick a framework** — they pay off immediately for the manual-prompt loop.

### 5.1 Eval harness completeness (Phase 11 partial — gap analysis)

Phase 11 sets up Langfuse datasets + LLM-as-judge + Python scoring scripts. **Gap to a learning loop:**

- Phase 11 scores at the run level. A learning loop needs **per-agent, per-call** scores.
- Phase 11 uses a static golden dataset. A learning loop needs **dataset rotation** — held-out validation that doesn't leak into prompt training.
- Phase 11 doesn't capture human-edit deltas. A learning loop's reward signal is largely *behavioral* (what did the human DO with this task?), not *judged* (what does the LLM judge say?).

**Required additions:** per-agent reward computation script that joins `audit.db` + final `pipeline_output.json` + (new) UI-edit-log + Jira-push outcome.

### 5.2 Feedback signal capture (THE BIGGEST GAP)

Today the UI captures *nothing* about what humans do with extracted tasks. We see "push happened" or "didn't". We don't see:

- Which fields were edited (title? AC? description?)
- Which tasks were deleted before push
- Which tasks were manually merged or split
- Which tasks the user re-ordered
- Whether the Jira issue got closed-as-done vs cancelled-as-out-of-scope

**Required work (NEW phase, ~ 1-2 engineer-weeks before any learning experiment):**

| Signal | Source | Storage |
|--------|--------|---------|
| Field-level edits | UI diff between server-returned `ManagedTask` and pushed payload | New `data/feedback.db` table or extension to `audit.db` |
| Pre-push deletions | UI action log | Same |
| Manual merges | UI action log | Same |
| Push outcome | Jira push response (already captured) | `JiraPushResult` |
| Issue resolution outcome | Jira webhook or periodic Jira API poll | NEW background poller |

The last one (resolution outcome) is the **long-feedback-loop signal** — most valuable but takes weeks/months to accumulate. Start collecting now even if we don't use it for 6 months.

### 5.3 Memory store evolution

Today: `data/project_indices/<project_key>/index.npz` is sentence-transformers embeddings, used for cross-run dedup only.

**Required for sector specialization:**
- Add a `sector` tag to `RunConfig` (currently absent).
- Maintain `data/sector_indices/<sector>/index.npz` parallel to project index.
- At extraction time, retrieve top-K exemplars from the customer index AND the sector index; prepend to the prompt.

**Required for customer memory:**
- The project index already serves this if we treat `jira_project_key` as the customer key (which is broadly true).
- Need richer payload than just embeddings: store the full `ManagedTask` payload tagged with "kept unchanged" / "edited" / "rejected" so we can retrieve *good examples* specifically.

### 5.4 Versioning + A/B routing

For any prompt optimization to be safe in production:

- **Prompt versioning** — each agent's prompt + few-shot pool gets a version ID, stored next to the run. Today they're constants in source.
- **A/B routing** — at run start, deterministically route a percentage of runs through "candidate" prompts vs "stable" prompts. Compare metrics per arm.
- **Rollback gate** — if candidate's reward drops below stable - 1 sigma over N runs, auto-revert.

The DSPy ecosystem partially handles this (serialize optimized programs to JSON, hot-swap). For A/B routing we'd need our own thin layer in `pipeline/llm_router.py` or a new `pipeline/prompt_router.py`.

### 5.5 Reward modeling (the data-firewall problem)

If we use audit-log-derived signal both as **training signal** and as **validation signal**, we will get falsely-optimistic results. Required:

- Time-based split: train on runs from months 1-3, validate on months 4-6, never optimize against validation. Re-roll periodically.
- Or, hold out by `jira_project_key`: optimize on 80% of customers, validate on 20%, rotate.

This is the cheapest mitigation against "the system that gets good at predicting past human edits is not the same as the system that gets good at producing tasks humans don't need to edit."

### 5.6 Cold-start handling

For a brand-new customer with no history and a sector we've never seen, what does the system do? **Recommended default behavior:**

1. Use generic prompts (today's baseline).
2. After N runs of the same `jira_project_key`, start retrieving from the customer index.
3. After M runs in a recognized sector, swap in sector-specific exemplars.
4. Display in UI "personalizing extraction based on prior runs" — make the learning *visible* so users trust it.

Pick N and M empirically; reasonable starting guesses N=3, M=10.

---

## 6. Risk register

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| **Catastrophic forgetting** — prompt updates that win on recent SOWs degrade old patterns | HIGH | HIGH | Held-out validation across SOW sectors; never optimize without a fixed evaluation slice that pre-dates training data |
| **Bias amplification** — early reviewers' edit biases (e.g., one reviewer who always tightens ACs) train the system to that taste | HIGH | MEDIUM | Multi-reviewer signal; weight by inter-reviewer agreement; require N≥3 reviewers before counting a "kept" task as positive signal |
| **Privacy / multi-tenancy data leakage** — Customer A's SOW patterns surface in Customer B's extraction via shared sector index | CRITICAL | MEDIUM | Sector index stores *abstracted patterns* (anonymized exemplars or hash-based embeddings), not raw text; per-customer index never leaves the customer's instance; consent gate on contributing to global sector indices |
| **Compute cost runaway** — RL/RFT loops can hit $5k/job ceiling fast | MEDIUM | LOW (if we stay in prompt-opt regime) | Phase out RL until necessary; budget envelope per experiment; hard caps in admin tooling |
| **Operational complexity** — adding learning infra is a 10× ops increment | HIGH | HIGH | Stage adoption (Section 7); keep everything in admin container, never in user container |
| **Vendor risk — agent-lightning sunset** — research-stage, fast-moving | MEDIUM | MEDIUM | Don't bet primary stack on it; pin version; choose DSPy as primary (Stanford-stewarded, broad adoption) |
| **Behavior drift** — silent prompt evolution drifts extraction toward unintended patterns | HIGH | MEDIUM | Mandatory A/B routing with reward-delta gate; weekly review of prompt diffs; manual veto power on auto-applied optimizations |
| **Eval contamination** — using audit logs as both training signal and validation | HIGH | HIGH (if we don't design it out) | Time-split or customer-split; per-experiment data manifest; documented firewall |
| **Memory poisoning** — adversarial SOW content alters customer/sector index | MEDIUM | LOW (today; HIGH if we expose multi-tenant SaaS) | Per-instance memory; admin-only writes to sector index after review; recent research (Anthropic 2025) shows even small poisoning samples affect models — assume hostile inputs |
| **Schema drift breaking downstream** — auto-optimized prompts emit subtly different `RawTask` shapes that break Pydantic validators | MEDIUM | MEDIUM | Pydantic strict-mode validation gate on every prompt-candidate before A/B routing |

Sources for the catastrophic-forgetting and poisoning risks:
- [Anthropic — Small samples can poison LLMs of any size](https://www.anthropic.com/research/small-samples-poison)
- [AgentPoison (NeurIPS 2024)](https://proceedings.neurips.cc/paper_files/paper/2024/file/eb113910e9c3f6242541c1652e30dfd6-Paper-Conference.pdf)
- [MemoryGraft: poisoned-experience retrieval attacks on LLM agents](https://arxiv.org/html/2512.16962v1)

---

## 7. Staged adoption path (4 stages, smallest first)

### Stage 1 — Sub-week experiment: "Does prompt optimization move the needle on ONE agent?"

**Hypothesis:** Running DSPy MIPROv2 (or GEPA, if time permits) on the extraction prompt against the Phase 11 hierarchical golden dataset improves extraction reward over the hand-written baseline.

**Scope:**
- Pick one agent: **extraction** (highest leverage).
- Use the Phase 11 golden dataset as both train (60%) and held-out validation (40%) split.
- Reward metric: F1 on extracted-task-set match against golden, weighted by node hierarchy depth.
- Run MIPROv2 in the admin container against `openrouter/google/gemini-2.5-flash` (same model as today's production runs).
- Compare optimized prompt against `EXTRACTION_SYSTEM_PROMPT` on held-out.

**Gate to Stage 2:**
- Optimized prompt scores ≥5% F1 improvement over baseline on held-out.
- No regression in any of the 6 success criteria from Phase 12 (zero criteria fall below baseline).
- Engineering effort estimate for Stage 2 is ≤ 4 engineer-weeks.

**Budget envelope:** 3-5 engineer-days. <$50 LLM cost.

**Rollback plan:** None needed — pure offline experiment, baseline prompt remains in source.

### Stage 2 — Sub-month investment: "Feedback capture + per-agent prompt versioning"

**Hypothesis:** With UI-edit signal captured and per-agent prompt versioning in place, we can run prompt optimization continuously and route a percentage of production runs through candidate prompts safely.

**Scope:**
- Build the feedback-capture layer (Section 5.2).
- Build prompt versioning + A/B routing (Section 5.4).
- Run DSPy MIPROv2 / GEPA against the *combined* Phase 11 golden + feedback-derived reward.
- Optimize the top 3 agents by leverage: extraction, classifier, critic.
- Keep stable arm at 80% traffic, candidate at 20%.

**Gate to Stage 3:**
- Candidate arm beats stable arm by ≥3% on the composite reward over ≥20 production runs.
- Feedback signal noise floor is characterized — i.e., we know the variance.
- No safety incidents (no candidate prompt drift caused customer-visible regressions).

**Budget envelope:** 3-4 engineer-weeks. ~$200/mo LLM cost for the A/B + optimization passes.

**Rollback plan:** Disable candidate arm via env flag (`SOW_PROMPT_AB_ENABLED=0`); fall back to constant prompts in source. Same shape as Phase 12's existing agent toggles.

### Stage 3 — Quarter-long investment: "Full DSPy migration on 3 agents + sector memory"

**Hypothesis:** Migrating extraction + classifier + critic to DSPy signatures, with sector-tagged exemplar retrieval, materially improves extraction quality vs. the optimized-prompt-only baseline from Stage 2.

**Scope:**
- Rewrite extraction, classifier, critic as DSPy `Module` subclasses with explicit `Signature` definitions.
- Build sector exemplar index (extension of cross_run_index.py).
- Add `sector` to `RunConfig`; UI prompt or autodetect at upload.
- Run side-by-side: current pipeline vs DSPy pipeline, on production traffic 50/50 split.
- Measure: same composite reward; secondary metrics on latency and cost.

**Gate to Stage 4:**
- DSPy pipeline beats current pipeline by ≥5% reward.
- Latency overhead <30% (DSPy adds overhead from compilation cache hits).
- Cost overhead acceptable (probably ~10-20% more LLM calls during the optimization pass; flat at inference).

**Budget envelope:** ~1 engineer-quarter (12-13 weeks). ~$500/mo LLM cost.

**Rollback plan:** Per-agent env flag to switch back to the source-prompt path; both paths stay maintained until full cutover.

### Stage 4 — Multi-quarter: "Pick the right hammer for what's left"

By the end of Stage 3, we have a clear picture of where prompt-space saturates. **Options at this point:**

**4a: Stay on DSPy, add sector-specific RFT.** For one or two highest-volume sectors, run OpenAI RFT on o4-mini to get a sector-specialized model. Route those sectors to the fine-tuned model. Cost ~$5k/sector, recurring on retraining cadence.

**4b: Migrate to agent-lightning for the agent(s) that need RL.** If by then agent-lightning is at v1.x with stable APIs and we have ≥1000 labeled trajectories per agent, the credit-assignment story becomes attractive. Migration cost: rewrite agents as agent-lightning-instrumented runners. Worth it only if 4a hits a wall.

**4c: Add the advisory agent (Section 3.7).** Independent of optimization-substrate choice. RAG over per-customer history + DSPy ChainOfThought.

**4d: Stop.** If Stage 3 metrics plateau and feedback signal density isn't growing, the right call is to bank the gains and shift engineering attention elsewhere.

**Decide between 4a/4b/4c/4d based on Stage 3 data**, not in advance.

---

## 8. Decision framework (for revisit in 3-6 months)

When the user re-reads this artifact, populate the matrix:

| Factor | Current state (May 2026) | Favors agent-lightning if... | Favors DSPy if... | Favors do-nothing if... |
|--------|--------------------------|-------------------------------|--------------------|--------------------------|
| Audit log volume | ~2 runs | ≥1000 labeled trajectories per agent across ≥3 sectors | ≥100 labeled trajectories total | <50 trajectories total |
| Eval harness maturity | Phase 11 pending | Per-agent, per-call scoring + dataset rotation operational | Run-level scoring + golden dataset operational | Manual review only |
| Sector specialization need | 1 sector observed | ≥3 sectors with ≥100 runs each AND prompt-space saturated | ≥2 sectors with ≥20 runs each | <2 sectors OR uncertain sector taxonomy |
| Team capacity | Single engineer | 2+ engineers for 6+ months | 1 engineer for 1 quarter | <1 engineer-quarter available |
| agent-lightning vendor health | v0.3, MS-stewarded, beta | v1.x released, ≥1 production case study published outside MS | (not a factor) | Project shows decline signal (commit cadence drop, no major releases in 6mo) |
| DSPy vendor health | v3.2, production-deployed | (not a factor) | Continued production adoption, GEPA/MIPRO maturity | Project shows decline signal |
| Cost envelope | ~$50/mo LLM | Budget ≥$2k/mo for training; GPU access | Budget ≥$200/mo for optimization passes | Budget <$100/mo |
| Competitive landscape | DSPy dominant for prompt-opt; agent-lightning leads RL story | New framework emerges that solves multi-agent RL with prompt-opt-class cost | Status quo: prompt-opt frameworks dominate | Hybrid frameworks emerge that obviate explicit optimizer choice |

**Decision rule:** If ≥5 rows favor a single column, that's your direction. If split, default to the lower-cost option (DSPy > agent-lightning > do-nothing) — value of information from doing Stage 1-2 dominates the value of being right in advance.

---

## 9. Honest verdict (May 2026)

**agent-lightning is not the right substrate for the next 6 months of SOW-to-Jira evolution.** The framework is impressive and architecturally distinctive — the training/agent disaggregation is a real idea — but it solves the *RL-on-weights* problem, and our problem is *prompt-space optimization on six small LLM calls* with very thin per-call signal. We are 12-18 months and ~1000 labeled trajectories away from the regime where RL-on-weights pays off over prompt evolution.

**The right answer for the 3-6 month horizon is DSPy + GEPA**, optimizing the extraction agent first, with feedback capture and A/B prompt routing as the load-bearing infrastructure prerequisites. The GEPA paper's own benchmark — beating GRPO by 6-20% with 35× fewer rollouts — is the evidence that prompt evolution dominates RL at our scale and signal density.

**The single highest-ROI move that requires no framework decision** is shipping UI feedback capture (Section 5.2). Without it, every optimization strategy is starved for signal; with it, the question of "which framework" becomes much easier to answer empirically through Stage 1 and Stage 2. **Do that first regardless of how this debate resolves.**

Revisit this artifact when Phase 12 ships, ≥20 production runs are accumulated, and the Phase 11 eval harness produces stable per-agent scores. At that point the Stage 1 experiment from Section 7 is the right next move; outcome of Stage 1 determines whether Stage 2 is worth the engineer-month investment.

---

## Sources

### Microsoft agent-lightning
- [GitHub: microsoft/agent-lightning](https://github.com/microsoft/agent-lightning)
- [arXiv:2508.03680 — Agent Lightning: Train ANY AI Agents with Reinforcement Learning](https://arxiv.org/abs/2508.03680)
- [Microsoft Research blog: Agent Lightning announcement](https://www.microsoft.com/en-us/research/blog/agent-lightning-adding-reinforcement-learning-to-ai-agents-without-code-rewrites/)
- [Microsoft Research project page](https://www.microsoft.com/en-us/research/project/agent-lightning/)
- [Agent Lightning docs](https://microsoft.github.io/agent-lightning/latest/)
- [MarkTechPost coverage (Oct 2025)](https://www.marktechpost.com/2025/10/29/microsoft-releases-agent-lightning-a-new-ai-framework-that-enables-reinforcement-learning-rl-based-training-of-llms-for-any-ai-agent/)

### DSPy + GEPA + MIPROv2
- [DSPy docs](https://dspy.ai/)
- [DSPy GitHub](https://github.com/stanfordnlp/dspy)
- [DSPy MIPROv2 docs](https://dspy.ai/api/optimizers/MIPROv2/)
- [DSPy GEPA docs](https://dspy.ai/api/optimizers/GEPA/overview/)
- [GEPA paper (arXiv:2507.19457) — ICLR 2026 Oral](https://arxiv.org/abs/2507.19457)
- [GEPA GitHub](https://github.com/gepa-ai/gepa)
- [MIPROv2 paper (arXiv:2406.11695)](https://arxiv.org/abs/2406.11695)
- [Databricks: Optimizing LLM Pipelines with DSPy](https://www.databricks.com/blog/optimizing-databricks-llm-pipelines-dspy)

### TextGrad
- [TextGrad GitHub (zou-group)](https://github.com/zou-group/textgrad)
- [TextGrad paper (arXiv:2406.07496) — published in Nature 2025](https://arxiv.org/abs/2406.07496)
- [Stanford HAI: TextGrad announcement](https://hai.stanford.edu/news/textgrad-autograd-text)

### OpenAI RFT
- [Reinforcement Fine-Tuning docs](https://platform.openai.com/docs/guides/reinforcement-fine-tuning)
- [RFT billing guide](https://help.openai.com/en/articles/11323177-billing-guide-for-the-reinforcement-fine-tuning-api)
- [RFT use cases](https://platform.openai.com/docs/guides/rft-use-cases)

### Framework comparisons
- [TextGrad vs DSPy (Medium)](https://medium.com/@jelkhoury880/textgrad-vs-dspy-revolutionizing-ai-system-optimization-through-automatic-text-based-58f8ee776447)
- [Agent Lightning vs SuperOptiX (Medium)](https://medium.com/superagentic-ai/agent-lightning-vs-superoptix-microsoft-enters-the-agent-optimization-race-c97fa3a9472f)
- [Best AI Agent Frameworks in 2025 (LangWatch)](https://langwatch.ai/blog/best-ai-agent-frameworks-in-2025-comparing-langgraph-dspy-crewai-agno-and-more)
- [LLM Frameworks Compared 2026 (Morph)](https://www.morphllm.com/llm-frameworks)

### Risk / safety
- [Anthropic — Small samples can poison LLMs of any size](https://www.anthropic.com/research/small-samples-poison)
- [AgentPoison (NeurIPS 2024)](https://proceedings.neurips.cc/paper_files/paper/2024/file/eb113910e9c3f6242541c1652e30dfd6-Paper-Conference.pdf)
- [MemoryGraft (arXiv 2512.16962)](https://arxiv.org/html/2512.16962v1)
- [Mechanistic Analysis of Catastrophic Forgetting](https://arxiv.org/html/2601.18699v1)
