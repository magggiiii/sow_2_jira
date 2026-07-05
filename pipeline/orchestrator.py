# pipeline/orchestrator.py

import concurrent.futures
import json
from pathlib import Path
from rich.progress import Progress, SpinnerColumn, TextColumn
import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.ports import LLMProvider, ObjectStore, RunRepository, TaskRepository

from models.schemas import (
    RunConfig, ManagedTask, TaskStatus, SourceRef, JiraHierarchy, current_provider_config
)
from pipeline.indexer import DocumentIndexer
from pipeline.coverage import CoverageTracker
from pipeline.llm_client import LLMClient
from pipeline.llm_router import configure_litellm_for_mode
from pipeline.agents.extraction import TaskExtractionAgent
from pipeline.agents.state import TaskStateAgent
from pipeline.agents.deduplication import DeduplicationAgent
from pipeline.agents.gap_recovery import GapRecoveryAgent
from pipeline.agents.classifier import SectionClassifier
from pipeline.agents.critic import TaskCritic
from pipeline.agents.coverage_check import CoverageChecker
from audit.logger import AuditLogger
from core.health import RunHealthReport, build_health_report
from core.guardrails import CoverageGate
from core.pipeline import PipelineContext, PipelineRunner
from pipeline.observability import logger, tracer, trace_span, sync_telemetry
from pipeline.telemetry import TelemetryEmitter
import os
import threading


from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn, MofNCompleteColumn

console = Console()


def cap_nodes(nodes: list[dict], max_nodes: int, strategy: str = "degraded") -> tuple[list[dict], bool, str]:
    """
    Apply the ``max_nodes`` Denial-of-Wallet cap to the node list.

    Returns ``(capped_nodes, capacity_degraded, reason)``. When the node count is
    within the cap, returns the list unchanged, ``False``, ``""``. When it exceeds
    the cap:

    - ``"strict"`` raises ``RuntimeError`` (legacy hard-stop, no partial output).
    - ``"degraded"`` (default) returns ``nodes[:max_nodes]`` with ``True`` and a
      ``"Processed N of M nodes"`` reason so the run keeps partial output and
      flags itself DEGRADED_CAPACITY instead of crashing.
    """
    total = len(nodes)
    if total <= max_nodes:
        return nodes, False, ""

    if strategy == "strict":
        raise RuntimeError(
            f"Denial of Wallet Protection: PDF generated {total} sections, "
            f"max allowed is {max_nodes}."
        )

    reason = f"Processed {max_nodes} of {total} nodes (capacity cap)"
    return nodes[:max_nodes], True, reason


class _CancelSignal:
    """2.3-d: a ``threading.Event``-shaped cancel bridge.

    The indexer and LLMClient only consult ``stop_event.is_set()`` to decide
    whether to bail / raise. A durable worker injects a custom ``cancel_check``
    (e.g. a Redis ``cancel:{run_id}`` probe) that does NOT flip the local
    ``stop_event``. This bridge reports ``is_set() == True`` when EITHER the local
    stop_event is set OR the injected cancel_check fires, so cancellation
    propagates into the indexer + any in-flight LLM call without touching their
    code. A misbehaving probe must never crash a run, so a probe exception is
    treated as 'not cancelled' (matching :meth:`PipelineOrchestrator._cancelled`).
    ``set()`` forwards to the real stop_event so anything already holding it keeps
    working.
    """

    def __init__(self, stop_event, cancel_check):
        self._stop_event = stop_event
        self._cancel_check = cancel_check

    def is_set(self) -> bool:
        if self._stop_event is not None and self._stop_event.is_set():
            return True
        try:
            return bool(self._cancel_check())
        except Exception:
            return False

    def set(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    def clear(self) -> None:
        if self._stop_event is not None:
            self._stop_event.clear()

    def wait(self, timeout=None):
        # Best-effort: delegate to the underlying event's wait so callers that
        # block on it keep their timeout semantics.
        if self._stop_event is not None:
            return self._stop_event.wait(timeout)
        return self.is_set()


class PipelineOrchestrator:

    def __init__(self, config: RunConfig, app_config: dict, audit: AuditLogger, status_callback=None, stop_event=None, llm: "LLMProvider | None" = None, cancel_check=None, object_store: "ObjectStore | None" = None, run_repo: "Optional[RunRepository]" = None, task_repo: "Optional[TaskRepository]" = None):
        self.config = config
        self.app_config = app_config
        self.audit = audit
        self.status_callback = status_callback
        self.stop_event = stop_event or threading.Event()
        # C2: cancellation seam. Defaults to the local stop_event (no behavior
        # change); a durable worker can inject e.g. a Redis ``cancel:{run_id}``
        # probe. Consulted between nodes so a run cancels cleanly mid-loop.
        self.cancel_check = cancel_check or self.stop_event.is_set

        # 2.3-d: a stop-event-shaped bridge that reports cancelled when EITHER the
        # local stop_event fires OR the injected cancel_check probe does. Handed
        # to the indexer + LLMClient below so a durable-worker cancel propagates
        # into the indexer (it bails) and any in-flight LLM call (it raises),
        # without either component needing to know about cancel_check.
        self._cancel_signal_obj = _CancelSignal(self.stop_event, self.cancel_check)

        # WAVE-0 1.6a: persistence seams. ``object_store`` is the run-artifact blob
        # store (pipeline_output.json, checkpoints, node_index, tree cache); when
        # omitted it DEFAULTS to a LocalObjectStore rooted at the existing data dir
        # so every current call site (main.py, ui/server.py) constructs the
        # orchestrator unchanged and behavior is byte-preserved. ``run_repo`` /
        # ``task_repo`` are the OPTIONAL DB write-through seam (1.6c) — no-op when
        # absent. Import the concrete LocalObjectStore lazily so this low-level
        # module keeps importing even before the durable stack is wired.
        if object_store is None:
            from integrations.object_store import LocalObjectStore
            object_store = LocalObjectStore(base_dir="data")
        self._object_store: "ObjectStore" = object_store
        self._run_repo: "Optional[RunRepository]" = run_repo
        self._task_repo: "Optional[TaskRepository]" = task_repo

        # LLM seam (HARNESS-4): use the injected provider when supplied,
        # otherwise construct today's default LLMClient with identical args.
        # 2.3-d: wire the cancel-aware signal (not the bare stop_event) as the
        # LLMClient's stop_event so a durable-worker cancel_check aborts in-flight
        # calls too — the client's existing ``self.stop_event.is_set()`` guard
        # then trips on either signal.
        self.llm: "LLMProvider" = llm if llm is not None else LLMClient(
            mode=config.llm_mode,
            audit_logger=audit,
            run_id=config.run_id,
            stop_event=self._cancel_signal_obj
        )

        # Build agents. STEP 5.4: each tuning knob resolves as
        # ``config field if set else env / app_config else legacy default`` — see
        # RunConfig. A None field reproduces the prior os.getenv behavior exactly.
        threshold = (
            config.extraction_confidence_threshold
            if config.extraction_confidence_threshold is not None
            else float(os.getenv("EXTRACTION_CONFIDENCE_THRESHOLD", "0.6"))
        )
        dedup_threshold = (
            config.dedup_similarity_threshold
            if config.dedup_similarity_threshold is not None
            else float(os.getenv("DEDUP_SIMILARITY_THRESHOLD", "0.85"))
        )
        max_gap_iter = (
            config.max_gap_recovery_iterations
            if config.max_gap_recovery_iterations is not None
            else app_config["pipeline"]["max_gap_recovery_iterations"]
        )
        max_section_chars = (
            config.max_section_chars
            if config.max_section_chars is not None
            else app_config["pipeline"].get("max_section_chars", 16000)
        )

        self.extraction_agent = TaskExtractionAgent(
            self.llm, audit, config.run_id, threshold, max_section_chars
        )
        self.state_agent = TaskStateAgent(audit, config.run_id)
        self.dedup_agent = DeduplicationAgent(
            self.llm, audit, config.run_id, dedup_threshold,
            project_key=(config.jira_project_key or None),
        )
        self.gap_agent = GapRecoveryAgent(self.llm, audit, config.run_id, max_gap_iter)

        # Intelligence-layer additions (260512-002). Each is togglable (config field
        # > env, default on) because each adds ~1 LLM call per section.
        classifier_enabled = (
            config.classifier_enabled
            if config.classifier_enabled is not None
            else os.getenv("SOW_CLASSIFIER_ENABLED", "1") != "0"
        )
        critic_enabled = (
            config.critic_enabled
            if config.critic_enabled is not None
            else os.getenv("SOW_ENABLE_CRITIC", "1") != "0"
        )
        semantic_coverage_enabled = (
            config.semantic_coverage_enabled
            if config.semantic_coverage_enabled is not None
            else os.getenv("SOW_SEMANTIC_COVERAGE", "1") != "0"
        )
        critic_threshold = (
            config.critic_threshold
            if config.critic_threshold is not None
            else float(os.getenv("SOW_CRITIC_THRESHOLD", "0.8"))
        )
        self.classifier = (
            SectionClassifier(self.llm, audit, config.run_id)
            if classifier_enabled else None
        )
        self.critic = (
            TaskCritic(
                self.llm, audit, config.run_id,
                auto_fix_threshold=critic_threshold,
            )
            if critic_enabled else None
        )
        self.coverage_checker = (
            CoverageChecker(
                self.llm, audit, config.run_id,
                max_section_chars=max_section_chars,
            )
            if semantic_coverage_enabled else None
        )
        self.section_coverage_reports: dict[str, dict] = {}
        # C-4 / STEP 3.3: result of the run-wide post-dedup coverage gate (set in
        # run() after dedup + gap recovery). Always present so consumers can read
        # it even before run() executes.
        self.coverage_gate_result = None
        # C-4 / STEP 3.5: dependency-injection seam for the coverage corpus filter's
        # embedder. None -> resolve the dedup MiniLM embedder lazily (only when the
        # filter is enabled); tests inject a deterministic fake here.
        self.coverage_filter_embed_fn = None

        # A2: per-node error isolation + graceful capacity capping. These are run
        # signals fed into the health report at the end of run().
        self.node_error_count: int = 0
        self.capacity_degraded: bool = False
        self.capacity_degraded_reason: str = ""

        # GUARDRAIL-3: per-run health summary, assembled at the end of run().
        # Starts empty (overall SKIPPED) so the attribute always exists even if
        # run() is never called or exits early.
        self.health_report: RunHealthReport = RunHealthReport()

        # Configure litellm for PageIndex and build indexer
        pageindex_model = configure_litellm_for_mode(config.llm_mode)
        self.indexer = DocumentIndexer(self.app_config, model=pageindex_model)
        self.telemetry = TelemetryEmitter()

    def _print_run_summary(self):
        """Prints a high-fidelity summary of the run configuration."""
        summary_text = (
            f"[bold cyan]Run ID:[/] {self.config.run_id}\n"
            f"[bold cyan]LLM Mode:[/] {self.config.llm_mode.value}\n"
            f"[bold cyan]Provider:[/] {self.config.provider_config.provider if self.config.provider_config else 'default'}\n"
            f"[bold cyan]Model:[/] {self.config.provider_config.model if self.config.provider_config else 'default'}\n"
            f"[bold cyan]Jira Hierarchy:[/] {self.config.jira_hierarchy.value}"
        )
        console.print(Panel(summary_text, title="[bold green]═══ SOW-to-Jira Pipeline ═══", border_style="green"))

    def _cancelled(self) -> bool:
        """C2: consult the cancellation seam (defaults to the local stop_event).
        Tolerant — a misbehaving probe must not crash the run; treat an error as
        'not cancelled' so the run continues rather than dying on a flaky check."""
        try:
            return bool(self.cancel_check())
        except Exception:
            return False

    def _cancel_signal(self):
        """2.3-d: the stop-event-shaped bridge handed to the indexer + LLMClient.
        Reports ``is_set()`` True when the local stop_event OR the injected
        cancel_check fires, so a durable-worker cancel propagates into both."""
        return self._cancel_signal_obj

    # ─── WAVE-0 1.6b: run-artifact persistence via the ObjectStore ───────────
    # All run-artifact I/O (status, tree cache, checkpoints, node_index,
    # pipeline_output, coverage_reports) goes through these helpers so the
    # orchestrator never does a direct open()/Path.write on data/sessions. Keys
    # are the stable session-relative paths (``sessions/<run_id>/<name>``), which
    # under the default LocalObjectStore(base_dir="data") resolve to the exact
    # same on-disk files as before — behavior-preserving for the local adapter.

    def _artifact_key(self, name: str) -> str:
        """Stable object key for a per-run artifact ``name``."""
        return f"sessions/{self.config.run_id}/{name}"

    def _put_artifact(self, key: str, data: bytes) -> str:
        """Persist ``data`` (bytes) under ``key`` through the object store."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        return self._object_store.put(key, data)

    def _put_json_artifact(self, key: str, obj, *, indent=None, default=None) -> str:
        """Serialize ``obj`` to JSON and persist it under ``key`` via the store."""
        text = json.dumps(obj, indent=indent, default=default)
        return self._put_artifact(key, text.encode("utf-8"))

    def _get_artifact(self, key: str) -> bytes:
        """Fetch the raw bytes stored under ``key`` (raises KeyError if absent)."""
        return self._object_store.get(key)

    def _artifact_exists(self, key: str) -> bool:
        """Return True if an artifact exists at ``key`` (store-backed)."""
        exists = getattr(self._object_store, "exists", None)
        if callable(exists):
            return bool(exists(key))
        try:
            self._object_store.get(key)
            return True
        except Exception:
            return False

    def _delete_artifact(self, key: str) -> None:
        """Best-effort delete of the artifact at ``key`` through the store."""
        # The ObjectStore Protocol only guarantees put/get/exists; a delete is
        # optional (LocalObjectStore.delete_run_prefix handles a single key too).
        deleter = getattr(self._object_store, "delete_run_prefix", None)
        if callable(deleter):
            try:
                deleter(key)
            except Exception:
                pass

    def _status_path(self) -> Path:
        return Path(f"data/sessions/{self.config.run_id}/status.json")

    def _mirror_status(self, step: int, message: str, progress: float) -> None:
        """C2: mirror live run status to the object store so partial progress is
        observable across a process restart even before the durable worker (C3).
        Best-effort — a write failure never affects the run. Routes through the
        store (1.6b) rather than a direct filesystem write."""
        try:
            self._put_json_artifact(
                self._artifact_key("status.json"),
                {"run_id": self.config.run_id, "step": step,
                 "message": message, "progress": progress, "ts": time.time()},
            )
        except Exception:
            pass

    def _update_status(self, step: int, message: str, progress: float = 0.0):
        if self.status_callback:
            self.status_callback(step, message, progress)
        self._mirror_status(step, message, progress)

    def _build_or_load_tree(self, pdf_path: str) -> list[dict]:
        # 1.6b: tree cache read/write routes through the object store.
        cache_key = self._artifact_key("document_tree.json")
        if self.config.skip_indexing and self._artifact_exists(cache_key):
            logger.info(f"› loading tree from cache: {cache_key}")
            tree = json.loads(self._get_artifact(cache_key).decode("utf-8"))
            self.indexer.last_tree = tree
            return self.indexer.flatten_tree(tree)

        with console.status("[bold blue]› Building document tree via PageIndex..."):
            # 2.3-d: pass the cancel-aware signal so an injected cancel_check
            # (e.g. a durable-worker probe) makes the indexer bail, not only the
            # local stop_event.
            nodes = self.indexer.build_tree(pdf_path, status_callback=self.status_callback, stop_event=self._cancel_signal(), run_id=self.config.run_id)

        # Cache for future skip-indexing runs
        if hasattr(self.indexer, "last_tree") and self.indexer.last_tree:
            self._put_json_artifact(cache_key, self.indexer.last_tree, indent=2)

        return nodes

    def _node_concurrency(self) -> int:
        """
        Resolve the per-node extract concurrency. STEP 5.4: ``config.node_concurrency``
        wins when set; otherwise fall back to ``SOW_NODE_CONCURRENCY`` (default 6).
        ``1`` reproduces the legacy strictly-sequential path; the ``max(1, ...)``
        floor applies to both paths. A malformed env value falls back to the default
        rather than crashing a run.
        """
        if self.config.node_concurrency is not None:
            return max(1, self.config.node_concurrency)
        try:
            return max(1, int(os.getenv("SOW_NODE_CONCURRENCY", "6")))
        except ValueError:
            return 6

    def _extract_node(self, node, node_index, total_nodes, status_callback=None):
        """
        Classify-gate + extract for ONE node. PURE per-node work: it depends only
        on ``node`` and its section text and mutates no shared run state, so it is
        safe to run concurrently across nodes (B1). Returns the raw task list, or
        ``None`` when the classifier gates the section out. ``status_callback`` is
        left ``None`` in the parallel path to avoid mutating the shared provider's
        ``status_callback`` from worker threads.
        """
        with tracer.start_as_current_span(f"EXTRACT_NODE_{node.get('node_id', 'none')}"):
            # Get text for this node (PageIndex provides it directly)
            section_text = self.indexer.get_node_text(node)

            # Classifier gate (Improvement #3): skip non-actionable sections
            if self.classifier is not None:
                classification = self.classifier.classify(node, section_text)
                if not self.classifier.should_extract(classification):
                    logger.info(
                        f"› Skipping non-actionable section: {node.get('title')} "
                        f"({classification.type.value}, conf={classification.confidence:.2f})"
                    )
                    return None

            # Extract (hierarchy-aware)
            return self.extraction_agent.extract(
                node, section_text,
                hierarchy=self.config.jira_hierarchy.value,
                status_callback=status_callback,
            )

    def _apply_node(self, node, raw_tasks, open_tasks):
        """
        Apply ONE node's extracted tasks in node order: StateAgent continuation
        merge → critic → semantic coverage. ORDER-SENSITIVE — the StateAgent
        continuation and the critic/coverage passes read the cross-node
        ``open_tasks`` accumulator, so this must run sequentially even when
        extraction is parallelized. Returns ``(open_tasks, newly_closed,
        section_tasks)``; performs no ``CoverageTracker`` mutation (the caller
        marks coverage in node order).
        """
        with tracer.start_as_current_span(f"APPLY_NODE_{node.get('node_id', 'none')}"):
            # Cheap re-read (PageIndex provides text directly) — critic + coverage
            # need the section text; not worth threading through the parallel phase.
            section_text = self.indexer.get_node_text(node)

            open_tasks, newly_closed = self.state_agent.process(
                raw_tasks, open_tasks, node
            )

            # Critic pass (Improvement #4): auto-fix or flag emitted tasks
            if self.critic is not None:
                emitted = open_tasks + newly_closed
                if emitted:
                    self.critic.critique(emitted, section_text, node)

            # Semantic coverage check (Improvement #2): what did we miss?
            # C-4 / STEP 3.3: the per-node loop only PRODUCES + stores the section
            # coverage report. INCOMPLETE flagging is deferred to the run-wide,
            # post-dedup CoverageGate (_run_coverage_verify) so a single confident
            # miss can no longer blanket-flag a section pre-dedup (the ~100%
            # INCOMPLETE bomb) and tasks are never double-flagged.
            section_tasks = open_tasks + newly_closed
            if self.coverage_checker is not None:
                report = self.coverage_checker.check_section(
                    node, section_text, section_tasks
                )
                if report.missed_items:
                    self.section_coverage_reports[node["node_id"]] = report.model_dump(mode="json")

            return open_tasks, newly_closed, section_tasks

    # ─── C-4 / STEP 3.3: run-wide, post-dedup coverage gate ─────────────────

    def _coverage_floor(self) -> float:
        """Confidence floor for the run-wide coverage gate.

        Defaults to the coverage checker's per-item ``min_confidence`` (0.6) so the
        run-wide gate and the legacy per-section predicate share one definition of
        "confident enough". STEP 5.4: ``config.coverage_min_confidence`` wins when
        set; otherwise override with ``SOW_COVERAGE_MIN_CONFIDENCE``.
        """
        if self.config.coverage_min_confidence is not None:
            return float(self.config.coverage_min_confidence)
        default = (
            self.coverage_checker.min_confidence
            if self.coverage_checker is not None
            else CoverageGate.DEFAULT_FLOOR
        )
        try:
            return float(os.getenv("SOW_COVERAGE_MIN_CONFIDENCE", str(default)))
        except (TypeError, ValueError):
            return float(default)

    def _coverage_filter_enabled(self) -> bool:
        """STEP 3.5: the embedding-tier corpus filter is OFF by default. Off keeps
        the INV-4 harness + existing gate tests byte-identical (the embedder is
        never sourced); production enables it via ``SOW_COVERAGE_CORPUS_FILTER=1``,
        which is also when the real-run INCOMPLETE rate actually drops. STEP 5.4:
        ``config.coverage_corpus_filter`` wins when set, else the env fallback."""
        if self.config.coverage_corpus_filter is not None:
            return self.config.coverage_corpus_filter
        return os.getenv("SOW_COVERAGE_CORPUS_FILTER", "0") == "1"

    def _coverage_embed_fn(self):
        """Resolve the corpus-filter embedder. A per-instance override
        (``coverage_filter_embed_fn``) wins so tests inject a deterministic fake
        without loading MiniLM; otherwise lazily reuse the dedup agent's ONE MiniLM
        embedder. Returns None when no embedder is available (filter then skipped)."""
        override = getattr(self, "coverage_filter_embed_fn", None)
        if override is not None:
            return override
        if self.dedup_agent is None:
            return None
        embedder = self.dedup_agent._get_embedder()  # reuse the single loaded model
        return lambda texts: embedder.encode(texts, normalize_embeddings=True)

    def _run_coverage_verify(self, tasks):
        """
        Apply INCOMPLETE to the FINAL (deduped + gap-recovered) task set,
        run-wide and report-level (audit C-4, STEP 3.3).

        A task is flagged only when one of its ``source_refs`` belongs to a section
        whose coverage report is confident (``checker_confidence >= floor``) AND has
        genuine ``missed_items``. This replaces the per-section, pre-dedup blanket
        flag that produced the ~100% INCOMPLETE bomb. Run-level advisory metadata is
        recorded on ``self.coverage_gate_result``.

        STEP 3.5: when ``SOW_COVERAGE_CORPUS_FILTER=1``, each reported miss is first
        tiered against the FINAL corpus by embedding similarity and the already-
        covered cross-section duplicates (sim >= 0.85) are dropped before the gate
        sees them — this is what drops the real-run INCOMPLETE rate. OFF by default,
        so the gate sees the reports unchanged (byte-identical to the structural fix).
        """
        gate = CoverageGate(floor=self._coverage_floor())
        reports = self.section_coverage_reports

        if self._coverage_filter_enabled() and self.section_coverage_reports:
            embed_fn = self._coverage_embed_fn()
            if embed_fn is not None:
                from pipeline.features.coverage_filter import CoverageCorpusFilter

                before = len(self.section_coverage_reports)
                surviving, audit = CoverageCorpusFilter(embed_fn).filter_reports(
                    self.section_coverage_reports, list(tasks)
                )
                # Persist the filtered view so the gate AND the saved
                # coverage_reports.json reflect the surviving (genuine) misses.
                self.section_coverage_reports = surviving
                reports = surviving
                try:
                    self.audit.log(
                        run_id=self.config.run_id,
                        agent="CoverageCorpusFilter",
                        node_id=None,
                        action="COVERAGE_CORPUS_FILTERED",
                        task_id=None,
                        detail=(
                            f"reports={before}->{len(surviving)} "
                            f"dropped={sum(a.dropped for a in audit)} "
                            f"overlap={sum(a.likely_overlap for a in audit)} "
                            f"uncovered={sum(a.uncovered for a in audit)}"
                        ),
                    )
                except Exception:
                    pass

        result = gate.apply(tasks, reports)
        self.coverage_gate_result = result

        if result.flagged_task_count:
            logger.info(
                f"› Coverage gate: flagged {result.flagged_task_count}/"
                f"{result.total_task_count} tasks INCOMPLETE across "
                f"{len(result.flagged_node_ids)} section(s) "
                f"(rate {result.incomplete_rate:.2f})"
            )
        try:
            self.audit.log(
                run_id=self.config.run_id,
                agent="CoverageGate",
                node_id=None,
                action="COVERAGE_GATE_APPLIED",
                task_id=None,
                detail=(
                    f"flagged={result.flagged_task_count} "
                    f"total={result.total_task_count} "
                    f"sections={len(result.flagged_node_ids)} "
                    f"rate={result.incomplete_rate:.3f} floor={gate.floor}"
                ),
            )
        except Exception:
            pass  # advisory metadata must never abort the run

        return result

    def _process_node(self, node, open_tasks, node_index, total_nodes):
        """
        Process ONE node end-to-end (extract then apply). Composition of
        :meth:`_extract_node` + :meth:`_apply_node`; returns
        ``(open_tasks, newly_closed, section_tasks)`` or ``None`` when gated. Used
        by the sequential path; the parallel path calls the two halves directly.
        """
        total = max(total_nodes, 1)
        current_node_progress = 0.40 + (0.40 * (node_index / total))
        node_title = node.get("title", f"Node {node_index}")
        status_cb = (
            lambda msg: self._update_status(
                3, f"Processing: {node_title[:30]}... ({msg})", current_node_progress
            )
        )
        raw_tasks = self._extract_node(node, node_index, total_nodes, status_callback=status_cb)
        if raw_tasks is None:
            return None
        return self._apply_node(node, raw_tasks, open_tasks)

    def _record_node_failure(self, node, node_index, exc) -> None:
        """Isolate a per-node failure: count it and record an audit row. Never
        re-raises — a single bad node must not abort the whole run (A2)."""
        self.node_error_count += 1
        logger.error(f"✗ Node {node.get('node_id', node_index)} failed to process: {exc}")
        try:
            self.audit.log(
                run_id=self.config.run_id,
                agent="Orchestrator",
                node_id=node.get("node_id", ""),
                action="EXTRACTION_FAILED",
                task_id=None,
                detail=f"Node processing failed, isolated and skipped: {exc}",
            )
        except Exception:
            pass  # audit must never mask the isolation it is recording

    # ─── C1: per-node checkpoint + resume ──────────────────────────────────

    def _extraction_checkpoint_path(self) -> Path:
        return Path(f"data/sessions/{self.config.run_id}/extraction_checkpoint.json")

    def _extraction_checkpoint_key(self) -> str:
        """Store key for the extraction checkpoint, derived from
        :meth:`_extraction_checkpoint_path` so a subclass/test override that
        redirects the path also redirects the store key. Returns ``None`` when the
        path resolves OUTSIDE the store's base dir (an override to e.g. tmp), in
        which case the caller falls back to a direct filesystem write at that
        literal path — preserving the legacy override contract."""
        return self._store_key_for_path(self._extraction_checkpoint_path())

    def _store_key_for_path(self, path: Path):
        """Return the store key for ``path`` if it lives under the object store's
        base dir, else ``None``. Only the default LocalObjectStore exposes a
        ``base_dir``; for any other store we assume the artifact key is the
        session-relative form and route through the store."""
        base = getattr(self._object_store, "base_dir", None)
        if base is None:
            # Non-filesystem store: use the stable session-relative key.
            return f"sessions/{self.config.run_id}/{Path(path).name}"
        try:
            rel = Path(path).resolve().relative_to(Path(base).resolve())
            return rel.as_posix()
        except (ValueError, OSError):
            return None  # path is outside the store base → legacy direct I/O

    def _write_extraction_checkpoint(self, nodes, last_index, all_closed_tasks, open_tasks, coverage) -> None:
        """
        Persist resume state after node ``last_index`` through the object store
        (1.6b). Holds the node-id list (for node-set validation on resume), the
        closed/open task sets, and coverage state. Best-effort: a checkpoint write
        failure must never abort the run — durability is a safety net, not a hard
        dependency. No-op when resumption is disabled (legacy single end-of-run
        checkpoint).
        """
        if not getattr(self.config, "enable_resumption", True):
            return
        try:
            payload = {
                "node_ids": [n["node_id"] for n in nodes],
                "last_index": last_index,
                "all_closed_tasks": [t.model_dump(mode="json") for t in all_closed_tasks],
                "open_tasks": [t.model_dump(mode="json") for t in open_tasks],
                "coverage": coverage.to_dict(),
                # C-4: the run-wide post-dedup coverage gate reads these, so they
                # must survive a resume — else pre-crash sections are under-flagged.
                "section_coverage_reports": dict(self.section_coverage_reports),
                "ts": time.time(),
            }
            key = self._extraction_checkpoint_key()
            if key is not None:
                self._put_json_artifact(key, payload, default=str)
            else:
                # Override points outside the store base → legacy direct write.
                path = self._extraction_checkpoint_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                with open(tmp, "w") as f:
                    json.dump(payload, f, default=str)
                os.replace(tmp, path)
        except Exception as e:
            logger.warning(f"Could not write extraction checkpoint: {e}")

    def _load_extraction_checkpoint(self) -> dict | None:
        try:
            key = self._extraction_checkpoint_key()
            if key is not None:
                if not self._artifact_exists(key):
                    return None
                return json.loads(self._get_artifact(key).decode("utf-8"))
            # Legacy direct read (path overridden outside the store base).
            path = self._extraction_checkpoint_path()
            if not path.exists():
                return None
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not read extraction checkpoint: {e}")
            return None

    def _delete_extraction_checkpoint(self) -> None:
        try:
            key = self._extraction_checkpoint_key()
            if key is not None:
                self._delete_artifact(key)
            else:
                self._extraction_checkpoint_path().unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Could not delete extraction checkpoint: {e}")

    def _maybe_resume(self, nodes, coverage):
        """
        Decide whether to resume from a prior extraction checkpoint.

        Returns ``(start_index, all_closed_tasks, open_tasks)``. When resumption is
        disabled, no checkpoint exists, the node-set has changed, or the payload is
        unreadable, returns ``(0, [], [])`` (fresh run) — deleting a stale/mismatched
        checkpoint so it can't corrupt a later run. On a valid match, restores the
        closed/open tasks + coverage and returns the index of the first not-yet-done
        node.
        """
        if not getattr(self.config, "enable_resumption", True):
            return 0, [], []
        data = self._load_extraction_checkpoint()
        if not data:
            return 0, [], []

        if data.get("node_ids") != [n["node_id"] for n in nodes]:
            logger.warning("› Resume checkpoint node-set mismatch — starting fresh")
            self._delete_extraction_checkpoint()
            return 0, [], []

        try:
            all_closed = [ManagedTask.model_validate(d) for d in data.get("all_closed_tasks", [])]
            open_tasks = [ManagedTask.model_validate(d) for d in data.get("open_tasks", [])]
        except Exception as e:
            logger.warning(f"› Resume checkpoint unreadable ({e}) — starting fresh")
            self._delete_extraction_checkpoint()
            return 0, [], []

        coverage.restore_from(data.get("coverage", {}))
        # C-4: restore the per-section coverage reports so the post-dedup gate sees
        # the sections processed before the crash, not only the post-resume ones.
        self.section_coverage_reports = dict(data.get("section_coverage_reports") or {})
        start_index = int(data.get("last_index", -1)) + 1
        logger.info(
            f"› Resuming run: {start_index}/{len(nodes)} nodes already processed "
            f"({len(all_closed)} tasks restored)"
        )
        return start_index, all_closed, open_tasks

    def _extract_all(self, nodes, coverage, *, start_index=0, all_closed_tasks=None, open_tasks=None):
        """
        Run extraction over all nodes with per-node error isolation, returning
        ``(all_closed_tasks, open_tasks, cancelled)``.

        B1: when ``SOW_NODE_CONCURRENCY`` > 1, the pure classify+extract work runs
        concurrently across nodes (a thread pool — the litellm seam is sync +
        thread-safe), then the order-sensitive apply (StateAgent merge, critic,
        coverage, coverage-marking) runs sequentially in node order. Output is
        identical to the sequential path. ``=1`` reproduces the legacy loop
        exactly. A node that raises (in either phase) is isolated via
        :meth:`_record_node_failure` and skipped. ``cancelled`` is ``True`` when
        the user stop_event fired.
        """
        concurrency = self._node_concurrency()
        total = len(nodes)
        # Seed accumulators — a resume restores prior tasks; a fresh run is empty.
        all_closed_tasks: list[ManagedTask] = list(all_closed_tasks or [])
        open_tasks: list[ManagedTask] = list(open_tasks or [])

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console
        ) as progress:
            task_bar = progress.add_task(
                "[cyan]Extracting nodes...", total=total, completed=start_index
            )

            # ── Sequential path (legacy; SOW_NODE_CONCURRENCY=1) ──────────────
            if concurrency <= 1:
                for i in range(start_index, total):
                    node = nodes[i]
                    current_node_progress = 0.40 + (0.40 * (i / total)) if total else 0.40
                    if self._cancelled():
                        logger.warning(f"› Pipeline cancelled by user at node {i}")
                        self._update_status(3, "Cancelled by user", current_node_progress)
                        return all_closed_tasks, open_tasks, True

                    node_title = node.get('title', f"Node {i}")
                    progress.update(task_bar, description=f"[cyan]Node: [bold]{node_title[:30]}...[/]")
                    self._update_status(3, f"Processing: {node_title[:50]}", current_node_progress)

                    try:
                        result = self._process_node(node, open_tasks, i, total)
                        if result is not None:
                            open_tasks, newly_closed, section_tasks = result
                            for task in section_tasks:
                                coverage.mark_covered(node["node_id"], str(task.id))
                            all_closed_tasks.extend(newly_closed)
                    except Exception as e:
                        self._record_node_failure(node, i, e)
                    # C1: checkpoint after each node (incl. gated/failed) so a
                    # resume skips it. Best-effort; never aborts the run.
                    self._write_extraction_checkpoint(nodes, i, all_closed_tasks, open_tasks, coverage)
                    progress.advance(task_bar)

                return all_closed_tasks, open_tasks, False

            # ── Parallel path (SOW_NODE_CONCURRENCY>1) ────────────────────────
            if self._cancelled():
                return all_closed_tasks, open_tasks, True

            self._update_status(3, f"Extracting {total} nodes (concurrency={concurrency})...", 0.40)

            # Phase 1: extract concurrently (pure per-node; status_callback=None
            # so no worker thread mutates the shared provider's callback). On a
            # resume, only the not-yet-done nodes (start_index:) are extracted.
            raw_by_index: list = [None] * total
            failed: set[int] = set()
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
                future_to_i = {
                    ex.submit(self._extract_node, nodes[i], i, total, None): i
                    for i in range(start_index, total)
                }
                for fut in concurrent.futures.as_completed(future_to_i):
                    i = future_to_i[fut]
                    try:
                        raw_by_index[i] = fut.result()  # list (success) or None (gated)
                    except Exception as e:
                        failed.add(i)
                        self._record_node_failure(nodes[i], i, e)
                    finally:
                        progress.advance(task_bar)

            # Phase 2: apply sequentially in node order (order-sensitive merge,
            # critic, coverage, coverage-marking), checkpointing after each node.
            for i in range(start_index, total):
                node = nodes[i]
                if self._cancelled():
                    logger.warning(f"› Pipeline cancelled by user at node {i}")
                    return all_closed_tasks, open_tasks, True
                if i not in failed:
                    raw_tasks = raw_by_index[i]
                    if raw_tasks is not None:  # None == classifier-gated
                        try:
                            open_tasks, newly_closed, section_tasks = self._apply_node(
                                node, raw_tasks, open_tasks
                            )
                            for task in section_tasks:
                                coverage.mark_covered(node["node_id"], str(task.id))
                            all_closed_tasks.extend(newly_closed)
                        except Exception as e:
                            self._record_node_failure(node, i, e)
                # C1: checkpoint after each node (incl. gated/failed) so a resume
                # skips it. Best-effort; never aborts the run.
                self._write_extraction_checkpoint(nodes, i, all_closed_tasks, open_tasks, coverage)

        return all_closed_tasks, open_tasks, False

    @trace_span("PIPELINE_RUN", agent="Orchestrator")
    def run(self) -> list[ManagedTask]:
        """
        Full pipeline. Returns final task list ready for review UI.
        Saves checkpoint to data/pipeline_output.json after completion.

        STEP 3.7: run() is now a thin, explicit sequencer over the ``_stage_*``
        phase methods, threading one :class:`PipelineContext`. ``run_via_pipeline``
        runs the SAME phase methods via the :class:`StageRegistry`; the two are
        equivalence-tested offline. ``config.use_pipeline_runner`` opts a run into
        the registry-driven path. ``run()`` is NOT deleted (gated on STEP 3.8).
        """
        if self.config.use_pipeline_runner:
            return self.run_via_pipeline()

        ctx = PipelineContext(orch=self)
        self._stage_setup(ctx)
        self._stage_index(ctx)
        self._stage_extract(ctx)
        if ctx.cancelled:
            return ctx.result()
        self._stage_dedup(ctx)
        self._stage_gap_recovery(ctx)
        self._stage_coverage_gate(ctx)
        self._stage_health(ctx)
        self._stage_save(ctx)
        return ctx.result()

    def run_via_pipeline(self) -> list[ManagedTask]:
        """STEP 3.7: run the pipeline via the staged :class:`PipelineRunner` — the
        same ``_stage_*`` phases as run(), sequenced from the registry, threading
        one PipelineContext (the runner stops early if a stage sets cancelled).
        Equivalence with run() is proved in tests/test_pipeline_runner_equivalence.py."""
        from pipeline.stages import build_default_registry

        ctx = PipelineContext(orch=self)
        PipelineRunner(build_default_registry()).run(ctx)
        return ctx.result()

    # ─── STEP 3.7: run() phases, extracted as ctx-threaded stage methods ──────
    # Each method holds the EXACT code the legacy run() body ran for that phase,
    # rebinding its locals onto the shared PipelineContext. run() (above) and the
    # registry-driven runner both call these, so behavior is single-sourced.

    def _stage_setup(self, ctx: PipelineContext) -> None:
        sync_telemetry()
        # Ensure provider_config is loaded and set ContextVar for this run/thread
        if not self.config.provider_config:
            self.config.provider_config = configure_litellm_for_mode(self.config.llm_mode)

        current_provider_config.set(self.config.provider_config)

        # Re-init components that need the resolved config
        self.llm.provider_config = self.config.provider_config
        self.llm.model = self.config.provider_config.model
        self.indexer.model = self.config.provider_config.model

        self._print_run_summary()

        self.telemetry.emit("run.started", {
            "run_id": self.config.run_id,
            "llm_mode": self.config.llm_mode.value,
            "jira_hierarchy": self.config.jira_hierarchy.value,
            "max_nodes": self.config.max_nodes,
            "filename": Path(self.config.sow_pdf_path).name,
        })
        ctx.run_start = time.time()

    def _stage_index(self, ctx: PipelineContext) -> None:
        # ── Step 1-2: Parse + Index via PageIndex ────────────────────────────
        with tracer.start_as_current_span("STEP_1_2_PAGEINDEX"):
            step_start = time.time()
            self._update_status(1, "Running PageIndex (parse + tree build)...", 0.05)
            logger.info("› Step 1/5: Running PageIndex...")
            nodes = self._build_or_load_tree(self.config.sow_pdf_path)
            self.telemetry.emit("step.completed", {
                "run_id": self.config.run_id,
                "step": "pageindex",
                "duration_ms": int((time.time() - step_start) * 1000),
                "node_count": len(nodes),
            })

        # ── Step 2: Initialize Coverage Tracker & Safety Check ───────────────
        # A2: graceful capacity capping. In the default "degraded" strategy an
        # over-cap document is processed up to max_nodes (partial output kept,
        # run flagged DEGRADED_CAPACITY) instead of crashing the whole run; the
        # "strict" strategy still raises the legacy Denial-of-Wallet RuntimeError.
        nodes, self.capacity_degraded, self.capacity_degraded_reason = cap_nodes(
            nodes, self.config.max_nodes, self.config.node_processing_strategy
        )
        if self.capacity_degraded:
            logger.warning(f"› {self.capacity_degraded_reason}")

        # Persist node hierarchy lookup so downstream consumers (JiraClient,
        # Wave-2 agents) can resolve parent titles without re-walking the tree.
        try:
            node_index_payload = {
                n["node_id"]: {
                    "title": n.get("title", ""),
                    "parent_id": n.get("parent_id"),
                    "parent_chain": list(n.get("parent_chain") or []),
                    "depth": int(n.get("depth") or 0),
                    "node_index": int(n.get("node_index") or 0),
                    "page_start": n.get("page_start"),
                    "page_end": n.get("page_end"),
                }
                for n in nodes if n.get("node_id")
            }
            # 1.6b: route through the object store (was a direct data/sessions write).
            self._put_json_artifact(
                self._artifact_key("node_index.json"), node_index_payload, indent=2
            )
        except Exception as e:
            # Non-fatal: JiraClient has a fallback path when this artifact is absent.
            logger.warning(f"Could not persist node_index.json: {e}")

        self._update_status(2, "Initializing coverage tracker...", 0.30)
        logger.info("› Step 2/5: Initializing coverage tracker...")
        coverage = CoverageTracker(nodes)
        self.telemetry.emit("step.completed", {
            "run_id": self.config.run_id,
            "step": "coverage_init",
            "duration_ms": 0,
            "node_count": len(nodes),
        })
        ctx.nodes = nodes
        ctx.coverage = coverage

    def _stage_extract(self, ctx: PipelineContext) -> None:
        nodes = ctx.nodes
        coverage = ctx.coverage
        # ── Step 3: Chunk Loop — Extract + State per node ─────────────────────
        with tracer.start_as_current_span("STEP_3_EXTRACT_LOOP") as span:
            step_start = time.time()
            span.set_attribute("node_count", len(nodes))
            self._update_status(3, f"Extracting tasks from {len(nodes)} nodes...", 0.40)

            # C1: resume from a prior crash if a valid checkpoint exists (skip
            # done nodes, restore their tasks + coverage). Fresh run → (0, [], []).
            start_index, resume_closed, resume_open = self._maybe_resume(nodes, coverage)

            # A2: per-node error isolation lives in _extract_all — one node's
            # failure is logged + counted and the loop continues. Cancellation
            # returns whatever was extracted so far.
            all_closed_tasks, open_tasks, cancelled = self._extract_all(
                nodes, coverage,
                start_index=start_index,
                all_closed_tasks=resume_closed,
                open_tasks=resume_open,
            )
            ctx.all_closed_tasks = all_closed_tasks
            ctx.open_tasks = open_tasks
            if cancelled:
                ctx.cancelled = True
                return

            # Force-close remaining open tasks
            forced_closed = self.state_agent.close_all_remaining(open_tasks)
            all_closed_tasks.extend(forced_closed)
            self.telemetry.emit("step.completed", {
                "run_id": self.config.run_id,
                "step": "extraction",
                "duration_ms": int((time.time() - step_start) * 1000),
                "task_count": len(all_closed_tasks),
            })

        logger.success(f"✓ Extracted {len(all_closed_tasks)} raw tasks")

    def _stage_dedup(self, ctx: PipelineContext) -> None:
        # ── Step 4: Deduplication ─────────────────────────────────────────────
        with tracer.start_as_current_span("STEP_4_DEDUP"):
            step_start = time.time()
            self._update_status(4, "Deduplicating tasks...", 0.85)
            logger.info("› Step 4/5: Deduplicating tasks...")
            deduplicated = self.dedup_agent.deduplicate(ctx.all_closed_tasks)
            logger.info(f"✓ {len(deduplicated)} tasks after deduplication")
            self.telemetry.emit("step.completed", {
                "run_id": self.config.run_id,
                "step": "deduplication",
                "duration_ms": int((time.time() - step_start) * 1000),
                "task_count": len(deduplicated),
            })
        ctx.deduplicated = deduplicated

    def _stage_gap_recovery(self, ctx: PipelineContext) -> None:
        coverage = ctx.coverage
        deduplicated = ctx.deduplicated
        # ── Step 5b: Gap Recovery ─────────────────────────────────────────────
        report = coverage.coverage_report()
        logger.info(f"Coverage: {report['coverage_pct']}% ({report['covered_nodes']}/{report['total_nodes']} nodes)")

        if report["gap_nodes"] > 0:
            with tracer.start_as_current_span("STEP_4B_GAP_RECOVERY"):
                step_start = time.time()
                self._update_status(4, f"Gap recovery on {report['gap_nodes']} nodes...", 0.90)
                logger.info(f"› Running gap recovery on {report['gap_nodes']} uncovered nodes...")
                gaps = coverage.get_gaps(min_text_length=100)
                recovered_pairs = self.gap_agent.recover(gaps, self.indexer)

                if recovered_pairs:
                    recovered_managed = []
                    for raw_task, actual_node in recovered_pairs:
                        open_tasks_tmp, closed_tmp = self.state_agent.process(
                            [raw_task], [], actual_node
                        )
                        recovered_managed.extend(open_tasks_tmp + closed_tmp)

                    # Force close any still open
                    recovered_managed = self.state_agent.close_all_remaining(recovered_managed)

                    # Merge with main list and re-dedup
                    combined = deduplicated + recovered_managed
                    deduplicated = self.dedup_agent.deduplicate(combined)
                    logger.info(f"✓ After gap recovery: {len(deduplicated)} tasks")
                self.telemetry.emit("step.completed", {
                    "run_id": self.config.run_id,
                    "step": "gap_recovery",
                    "duration_ms": int((time.time() - step_start) * 1000),
                    "task_count": len(deduplicated),
                })
        ctx.report = report
        ctx.deduplicated = deduplicated

    def _stage_coverage_gate(self, ctx: PipelineContext) -> None:
        # ── Step 4c: run-wide coverage gate (C-4 / STEP 3.3) ──────────────────
        # Confidence-gated, report-level INCOMPLETE on the FINAL (deduped +
        # gap-recovered) task set. Mutates ``ctx.deduplicated`` in place.
        self._run_coverage_verify(ctx.deduplicated)

    def _stage_health(self, ctx: PipelineContext) -> None:
        report = ctx.report
        # ── Assemble run health summary (GUARDRAIL-3) ─────────────────────────
        # Additive, read-only: distill the signals the stages already produced
        # into a RunHealthReport.
        self.health_report = build_health_report({
            "dedup_degraded": getattr(self.dedup_agent, "last_degraded", False),
            "dedup_degraded_reason": getattr(self.dedup_agent, "last_degraded_reason", None),
            "extraction_error_count": getattr(self.extraction_agent, "error_count", 0),
            "coverage_pct": report.get("coverage_pct"),
            # A2: capacity capping + per-node error isolation signals.
            "capacity_degraded": self.capacity_degraded,
            "capacity_degraded_reason": self.capacity_degraded_reason,
            "node_error_count": self.node_error_count,
            "node_total": len(ctx.nodes),
        })

    def _write_through_repos(self, tasks, report) -> None:
        """WAVE-0 1.6c: mirror the run + task records into the injected DB repos.

        No-op when neither repo is injected (the default local path). NEVER
        persists a plaintext api_key/token: the run config is sanitized before it
        is handed to the run repo. Best-effort — a repo write failure must never
        abort a run whose artifacts already persisted to the object store.
        """
        if self._run_repo is None and self._task_repo is None:
            return

        run_id = self.config.run_id
        if self._run_repo is not None:
            try:
                run_data = {
                    "run_id": run_id,
                    "config": self._sanitized_config_dict(),
                    "coverage_report": report,
                    "health": self.health_report.to_dict(),
                    "task_count": len(tasks),
                }
                self._run_repo.create(run_id, run_data)
            except Exception as e:
                logger.warning(f"Run repo write-through failed: {e}")

        if self._task_repo is not None:
            for task in tasks:
                try:
                    self._task_repo.add(run_id, task)
                except Exception as e:
                    logger.warning(f"Task repo write-through failed: {e}")

    def _sanitized_config_dict(self) -> dict:
        """Return the run config as a dict with all secret material stripped.

        The only credential-bearing field on RunConfig is
        ``provider_config.api_key`` (see models.schemas.ProviderConfig); we scrub
        it (plus any defensively-named token/secret/password keys) so a plaintext
        credential is NEVER handed to the persistence repo. The object-store
        artifact keeps its own (already local-only) copy unchanged."""
        data = self.config.model_dump(mode="json")
        _SECRET_KEYS = {"api_key", "api_token", "token", "secret", "password"}

        def _scrub(obj):
            if isinstance(obj, dict):
                return {
                    k: ("" if k in _SECRET_KEYS else _scrub(v))
                    for k, v in obj.items()
                }
            if isinstance(obj, list):
                return [_scrub(v) for v in obj]
            return obj

        return _scrub(data)

    def _stage_save(self, ctx: PipelineContext) -> None:
        deduplicated = ctx.deduplicated
        report = ctx.report
        # ── Step 5: Save Checkpoint ───────────────────────────────────────────
        with tracer.start_as_current_span("STEP_5_SAVE"):
            step_start = time.time()
            self._update_status(5, "Saving pipeline output...", 0.95)
            logger.info("› Step 5/5: Saving pipeline output...")
            checkpoint_key = self._artifact_key("pipeline_output.json")

            # 1.6b: pipeline output routes through the object store (was a direct
            # data/sessions write).
            self._put_json_artifact(
                checkpoint_key,
                {
                    "run_id": self.config.run_id,
                    "config": self.config.model_dump(mode="json"),
                    "tasks": [t.model_dump(mode="json") for t in deduplicated],
                    "coverage_report": report,
                    # Additive key — existing consumers ignore it.
                    "health": self.health_report.to_dict(),
                },
                indent=2,
                default=str,
            )

            # Semantic coverage reports (Improvement #2) — sibling artifact
            if self.section_coverage_reports:
                self._put_json_artifact(
                    self._artifact_key("coverage_reports.json"),
                    self.section_coverage_reports,
                    indent=2,
                    default=str,
                )

            # 1.6c: mirror the run + task records into the DB repos (no-op when
            # they aren't injected). Never persists a plaintext api_key/token.
            self._write_through_repos(deduplicated, report)

            # C1: the run completed — the per-node resume checkpoint is no longer
            # needed, so a re-run starts fresh rather than resuming a finished run.
            self._delete_extraction_checkpoint()

            self.telemetry.emit("step.completed", {
                "run_id": self.config.run_id,
                "step": "save",
                "duration_ms": int((time.time() - step_start) * 1000),
                "task_count": len(deduplicated),
            })

        logger.success(f"✓ Pipeline complete! {len(deduplicated)} tasks ready.")
        logger.info(f"Checkpoint saved: {checkpoint_key}")
        self.telemetry.emit("run.completed", {
            "run_id": self.config.run_id,
            "duration_ms": int((time.time() - ctx.run_start) * 1000),
            "task_count": len(deduplicated),
            "coverage_pct": report["coverage_pct"],
        })

        sync_telemetry()
