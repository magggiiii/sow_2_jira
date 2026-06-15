#!/usr/bin/env python3
# scripts/check_structured_output.py
"""
Live smoke-gate for the C-5 per-agent Instructor structured-output path.

All six pipeline agents (classifier, critic, coverage_check, extraction,
gap_recovery, deduplication) now route their single model call through
``AgentRunner.complete_structured(response_model=…)`` — Instructor over LiteLLM,
returning a *validated* Pydantic model instead of regex-scraped JSON. That path
is exercised only with STUBS in the test suite (``tests/test_agent_runner_structured.py``
and the per-agent tests), so the real ``instructor → litellm → provider`` round
trip has never run end-to-end. This script closes that gap: it drives each agent
once against a real provider with a minimal payload that clears its early-return
guards, and reports whether the agent's structured call actually came back and
validated.

It also doubles as the recorder for the INV-4 eval cassette: ``--record <path>``
dumps each agent's validated output as JSON seed material (the full per-call
cassette capture is a later step; this is honest seed data, not the cassette).

WHAT THIS DOES NOT TOUCH (locked):
  - It does NOT modify ``pipeline/llm_router.py``, ``BIFROST_*``, or any
    ``os.environ`` credential path. It only *reads* ``configure_litellm_for_mode``
    (optional) or builds a ``ProviderConfig`` from CLI flags / env and hands it
    to ``LLMClient`` — exactly as ``main.py`` / the UI do.

USAGE
-----
  # Offline wiring check (no provider, no network) — stubs the Instructor boundary:
  venv/bin/python scripts/check_structured_output.py --self-test

  # Live run, explicit provider/model (recommended — bypasses settings.json):
  venv/bin/python scripts/check_structured_output.py \
      --provider openrouter --model "openrouter/z-ai/glm-4.6" \
      --api-key "$OPENROUTER_API_KEY"

  # Live run using whatever the UI settings / env already resolve for a mode:
  venv/bin/python scripts/check_structured_output.py --mode custom

  # Record validated outputs for the eval cassette:
  venv/bin/python scripts/check_structured_output.py --provider ... --model ... \
      --api-key ... --record data/structured_smoke.json

  # Skip dedup (it loads the sentence-transformers embedder, ~80MB):
  venv/bin/python scripts/check_structured_output.py --self-test --skip-dedup

EXIT CODES
----------
  0  every attempted agent reached complete_structured and validated.
  1  one or more agents failed (or dedup never formed a candidate pair).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

# Ensure repo root is importable when run as `python scripts/check_structured_output.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.schemas import (  # noqa: E402
    LLMMode,
    ManagedTask,
    ProviderConfig,
    SourceRef,
)
from pipeline.agents.classifier import RawClassification, SectionClassifier  # noqa: E402
from pipeline.agents.classifier import SectionType  # noqa: E402
from pipeline.agents.coverage_check import CoverageAudit, CoverageChecker  # noqa: E402
from pipeline.agents.critic import CritiqueBatch, TaskCritic  # noqa: E402
from pipeline.agents.deduplication import DedupDecisionList, DeduplicationAgent  # noqa: E402
from pipeline.agents.extraction import ExtractionResult, TaskExtractionAgent  # noqa: E402
from pipeline.agents.gap_recovery import GapRecoveryAgent, GapRecoveryResult  # noqa: E402


# ─── Minimal, guard-clearing payloads ─────────────────────────────────────────

# node carries every key the agents index directly (extraction/gap_recovery use
# node["page_start"]/["page_end"], so they must be present).
NODE = {
    "node_id": "smoke-1",
    "title": "User Management",
    "page_start": 1,
    "page_end": 2,
}

# >100 chars stripped so coverage_check / gap_recovery don't short-circuit, and
# concrete enough that extraction/classifier have real work to find.
SECTION_TEXT = (
    "The platform must let users register with an email and password, log in, "
    "and reset a forgotten password via an emailed one-time link. Admin users "
    "must additionally enrol in TOTP-based two-factor authentication, and an "
    "admin console must allow deactivating a user account. "
) * 2


def _src() -> SourceRef:
    return SourceRef(
        node_id="smoke-1",
        section_title="User Management",
        page_start=1,
        page_end=2,
    )


def _task(title: str, desc: str) -> ManagedTask:
    return ManagedTask(
        title=title,
        short_description=desc,
        confidence=0.9,
        source_refs=[_src()],
    )


class _Indexer:
    """Minimal indexer stub for gap_recovery: returns the section text."""

    def __init__(self, text: str):
        self._text = text

    def get_node_text(self, node: dict) -> str:
        return self._text


class SmokeAudit:
    """In-memory audit sink — keeps the smoke run out of data/audit.db."""

    def __init__(self):
        self.records: list[dict] = []

    def log(self, **kwargs):
        self.records.append(kwargs)

    def actions(self) -> list[str]:
        return [r.get("action") for r in self.records]


def _verdict(audit: SmokeAudit, success: set[str], error: set[str]) -> tuple[bool, str]:
    acts = audit.actions()
    has_ok = any(a in success for a in acts)
    has_err = any(a in error for a in acts)
    if has_err:
        # Surface the recorded failure detail for a useful message (full text —
        # callers truncate for display; --record keeps it whole for diagnosis).
        detail = next(
            (r.get("detail", "") for r in audit.records if r.get("action") in error),
            "",
        )
        return False, f"recorded {error & set(acts)}: {detail}"
    if not has_ok:
        return False, f"no success action {success} recorded; saw {sorted(set(acts))}"
    return True, f"ok ({success & set(acts)})"


def _jsonable(obj):
    """Best-effort JSON-serializable form of an agent output for --record."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, tuple):
        return [_jsonable(x) for x in obj]
    return obj


# ─── Per-agent smoke cases ─────────────────────────────────────────────────────
#
# Each case builds the agent, optionally stubs its complete_structured boundary
# (self-test), invokes the real entrypoint, then derives pass/fail from the
# agent's OWN audit actions (its observable success/failure contract).

def case_classifier(client, run_id, self_test, _tmp):
    audit = SmokeAudit()
    agent = SectionClassifier(client, audit, run_id)
    if self_test:
        agent.runner.complete_structured = lambda **k: RawClassification(
            type=SectionType.ACTIONABLE, confidence=0.9, reason="self-test"
        )
    result = agent.classify(NODE, SECTION_TEXT)
    ok, msg = _verdict(audit, {"CLASSIFIED"}, {"CLASSIFY_ERROR"})
    return ok, msg, result


def case_critic(client, run_id, self_test, _tmp):
    audit = SmokeAudit()
    agent = TaskCritic(client, audit, run_id)
    if self_test:
        agent.runner.complete_structured = lambda **k: CritiqueBatch(critiques=[])
    tasks = [_task("Implement user login API", "Authenticate users by email and password.")]
    _, report = agent.critique(tasks, SECTION_TEXT, NODE)
    ok, msg = _verdict(audit, {"CRITIQUE_RUN"}, {"CRITIQUE_LLM_ERROR"})
    return ok, msg, report


def case_coverage(client, run_id, self_test, _tmp):
    audit = SmokeAudit()
    agent = CoverageChecker(client, audit, run_id)
    if self_test:
        agent.runner.complete_structured = lambda **k: CoverageAudit(missed_items=[])
    extracted = [_task("Implement user login API", "Authenticate users by email and password.")]
    report = agent.check_section(NODE, SECTION_TEXT, extracted)
    ok, msg = _verdict(audit, {"COVERAGE_CHECK"}, {"COVERAGE_CHECK_ERROR"})
    return ok, msg, report


def case_extraction(client, run_id, self_test, _tmp):
    audit = SmokeAudit()
    agent = TaskExtractionAgent(client, audit, run_id)
    if self_test:
        agent.runner.complete_structured = lambda **k: ExtractionResult(tasks=[])
    tasks = agent.extract(NODE, SECTION_TEXT)
    ok, msg = _verdict(audit, {"EXTRACTED"}, {"EXTRACTION_ERROR"})
    return ok, msg, tasks


def case_gap_recovery(client, run_id, self_test, _tmp):
    audit = SmokeAudit()
    agent = GapRecoveryAgent(client, audit, run_id)
    if self_test:
        agent.runner.complete_structured = lambda **k: GapRecoveryResult(tasks=[])
    results = agent.recover([NODE], _Indexer(SECTION_TEXT))
    # A successful structured call logs RECOVERY_COMPLETE (found tasks) or
    # NO_GAPS_RECOVERED (responded, found none) — both mean the call returned.
    ok, msg = _verdict(
        audit, {"RECOVERY_COMPLETE", "NO_GAPS_RECOVERED"}, {"RECOVERY_ERROR"}
    )
    return ok, msg, results


def case_deduplication(client, run_id, self_test, tmp):
    audit = SmokeAudit()
    # threshold lowered so two near-identical tasks form a candidate pair and
    # the LLM confirmation call actually fires. sessions_dir → tmp so the smoke
    # run never writes into the real data/sessions tree.
    agent = DeduplicationAgent(
        client, audit, run_id,
        similarity_threshold=0.6,
        sessions_dir=str(Path(tmp) / "sessions"),
        project_indices_dir=str(Path(tmp) / "project_indices"),
    )
    if self_test:
        import numpy as np
        from types import SimpleNamespace
        agent._get_embedder = lambda: SimpleNamespace(
            encode=lambda texts, normalize_embeddings=True: np.zeros(
                (len(texts), 384), dtype=np.float32
            )
        )
        agent._find_candidate_pairs = lambda tasks, emb: [(tasks[0], tasks[1], 0.95)]
        agent.runner.complete_structured = lambda **k: DedupDecisionList(decisions=[])
    tasks = [
        _task("Implement user login API", "User logs in with email and password."),
        _task("Build login endpoint", "User logs in with email and password."),
    ]
    out = agent.deduplicate(tasks)
    acts = audit.actions()
    if "DEDUP_ERROR" in acts:
        return False, _verdict(audit, {"DEDUP_COMPLETE"}, {"DEDUP_ERROR"})[1], out
    if "DEDUP_COMPLETE" not in acts:
        # NO_DUPLICATES_FOUND → no candidate pair → the LLM path was not exercised.
        return False, (
            "no candidate pair formed (NO_DUPLICATES_FOUND) — embeddings did not "
            "clear threshold; structured call not exercised"
        ), out
    return True, "ok (DEDUP_COMPLETE)", out


CASES = [
    ("classifier", case_classifier),
    ("critic", case_critic),
    ("coverage_check", case_coverage),
    ("extraction", case_extraction),
    ("gap_recovery", case_gap_recovery),
    ("deduplication", case_deduplication),
]


# ─── Provider wiring ───────────────────────────────────────────────────────────

def build_client(args, run_id: str):
    """Build an LLMClient. Either an explicit ProviderConfig from flags/env, or
    (when no --model) resolved from the mode via configure_litellm_for_mode."""
    from pipeline.llm_client import LLMClient

    audit = SmokeAudit()  # client-level audit (LLM_CALL etc.); separate per agent below
    mode = LLMMode(args.mode)

    explicit_key = args.api_key or os.environ.get("S2J_SMOKE_API_KEY", "")
    provider_config = None

    if args.model and explicit_key:
        # Fully explicit: provider/model/key/base from flags + env.
        provider_config = ProviderConfig(
            provider=args.provider or "openai",
            model=args.model,
            api_key=explicit_key,
            api_base=args.api_base or "",
        )
    elif args.model:
        # Model override: resolve credentials (api_key/api_base) from the
        # existing settings/env via the router — READ-ONLY, no llm_router change
        # — then swap ONLY the model string. This keeps the secret out of the
        # command line while still letting us point the smoke-gate at a different
        # model than the one configured in settings.
        from pipeline.llm_router import configure_litellm_for_mode
        provider_config = configure_litellm_for_mode(mode)
        provider_config.model = args.model
        if args.provider:
            provider_config.provider = args.provider
        if args.api_base:
            provider_config.api_base = args.api_base
    # else: provider_config stays None → LLMClient resolves everything via the
    #       router (reads UI settings.json / env). No llm_router modification.

    return LLMClient(
        mode=mode,
        audit_logger=audit,
        run_id=run_id,
        provider_config=provider_config,
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--self-test", action="store_true",
                   help="Stub the Instructor boundary; verify wiring offline (no provider/network).")
    p.add_argument("--mode", default="custom", choices=[m.value for m in LLMMode],
                   help="LLMMode when no --model is given (resolved via settings/env). Default: custom.")
    p.add_argument("--provider", default=None, help="Provider name (e.g. openrouter, openai, anthropic, ollama).")
    p.add_argument("--model", default=None, help="Full litellm model string (e.g. 'openrouter/z-ai/glm-4.6').")
    p.add_argument("--api-key", default=None, help="Provider API key (or set S2J_SMOKE_API_KEY).")
    p.add_argument("--api-base", default=None, help="Provider API base URL (optional).")
    p.add_argument("--record", default=None, help="Write validated agent outputs to this JSON (cassette seed).")
    p.add_argument("--skip-dedup", action="store_true",
                   help="Skip the dedup case (avoids loading the sentence-transformers embedder).")
    p.add_argument("--only", default=None,
                   help="Comma-separated agent names to run (default: all). "
                        "e.g. --only classifier,extraction")
    args = p.parse_args()

    run_id = "smoke-structured"
    mode_label = "SELF-TEST (stubbed)" if args.self_test else f"LIVE mode={args.mode}"

    # Build the client up front (live only — self-test still builds it so the
    # agents have a provider with a resolvable .model, but never calls out).
    try:
        client = build_client(args, run_id)
    except Exception as e:
        print(f"✗ Could not build LLMClient: {e}", file=sys.stderr)
        return 1

    model = getattr(client, "model", "?")
    print(f"── Structured-output smoke-gate · {mode_label} · model={model} ──\n")

    selected = set(s.strip() for s in args.only.split(",")) if args.only else None
    tmp = tempfile.mkdtemp(prefix="s2j-smoke-")
    rows: list[tuple[str, bool, str, float]] = []
    recorded: dict = {}

    for name, fn in CASES:
        if selected is not None and name not in selected:
            continue
        if name == "deduplication" and args.skip_dedup:
            rows.append((name, True, "skipped (--skip-dedup)", 0.0))
            continue
        t0 = time.time()
        try:
            ok, msg, result = fn(client, run_id, args.self_test, tmp)
            recorded[name] = (
                _jsonable(result) if ok
                else {"failed": True, "detail": msg, "degraded_output": _jsonable(result)}
            )
        except Exception as e:
            ok, msg = False, f"EXCEPTION: {e}"
            recorded[name] = {"error": str(e)}
            if os.environ.get("S2J_SMOKE_TRACE"):
                traceback.print_exc()
        dt = time.time() - t0
        rows.append((name, ok, msg, dt))
        mark = "✓" if ok else "✗"
        # Full message when tracing (for diagnosis); otherwise one tidy line.
        shown = msg if (ok or os.environ.get("S2J_SMOKE_TRACE")) else msg[:160]
        print(f"  {mark} {name:<16} {dt:6.2f}s  {shown}")

    n_ok = sum(1 for _, ok, _, _ in rows if ok)
    n_total = len(rows)
    print(f"\n── {n_ok}/{n_total} agents passed ──")

    if args.record:
        Path(args.record).parent.mkdir(parents=True, exist_ok=True)
        Path(args.record).write_text(json.dumps(recorded, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"   recorded validated outputs → {args.record}")

    return 0 if n_ok == n_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
