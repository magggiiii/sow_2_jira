# pipeline/orchestrator.py

import concurrent.futures
import json
from pathlib import Path
from rich.progress import Progress, SpinnerColumn, TextColumn
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.ports import LLMProvider

from models.schemas import (
    RunConfig, ManagedTask, TaskStatus, TaskFlag, SourceRef, JiraHierarchy, current_provider_config
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
from pipeline.agents.coverage_check import CoverageChecker, should_flag_section_incomplete
from audit.logger import AuditLogger
from core.health import RunHealthReport, build_health_report
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


class PipelineOrchestrator:

    def __init__(self, config: RunConfig, app_config: dict, audit: AuditLogger, status_callback=None, stop_event=None, llm: "LLMProvider | None" = None, cancel_check=None):
        self.config = config
        self.app_config = app_config
        self.audit = audit
        self.status_callback = status_callback
        self.stop_event = stop_event or threading.Event()
        # C2: cancellation seam. Defaults to the local stop_event (no behavior
        # change); a durable worker can inject e.g. a Redis ``cancel:{run_id}``
        # probe. Consulted between nodes so a run cancels cleanly mid-loop.
        self.cancel_check = cancel_check or self.stop_event.is_set

        # LLM seam (HARNESS-4): use the injected provider when supplied,
        # otherwise construct today's default LLMClient with identical args.
        self.llm: "LLMProvider" = llm if llm is not None else LLMClient(
            mode=config.llm_mode,
            audit_logger=audit,
            run_id=config.run_id,
            stop_event=self.stop_event
        )

        # Build agents
        threshold = float(os.getenv("EXTRACTION_CONFIDENCE_THRESHOLD", "0.6"))
        dedup_threshold = float(os.getenv("DEDUP_SIMILARITY_THRESHOLD", "0.85"))
        max_gap_iter = app_config["pipeline"]["max_gap_recovery_iterations"]
        max_section_chars = app_config["pipeline"].get("max_section_chars", 16000)

        self.extraction_agent = TaskExtractionAgent(
            self.llm, audit, config.run_id, threshold, max_section_chars
        )
        self.state_agent = TaskStateAgent(audit, config.run_id)
        self.dedup_agent = DeduplicationAgent(
            self.llm, audit, config.run_id, dedup_threshold,
            project_key=(config.jira_project_key or None),
        )
        self.gap_agent = GapRecoveryAgent(self.llm, audit, config.run_id, max_gap_iter)

        # Intelligence-layer additions (260512-002). Each is togglable via env
        # because each adds ~1 LLM call per section.
        self.classifier = (
            SectionClassifier(self.llm, audit, config.run_id)
            if os.getenv("SOW_CLASSIFIER_ENABLED", "1") != "0" else None
        )
        self.critic = (
            TaskCritic(
                self.llm, audit, config.run_id,
                auto_fix_threshold=float(os.getenv("SOW_CRITIC_THRESHOLD", "0.8")),
            )
            if os.getenv("SOW_ENABLE_CRITIC", "1") != "0" else None
        )
        self.coverage_checker = (
            CoverageChecker(
                self.llm, audit, config.run_id,
                max_section_chars=max_section_chars,
            )
            if os.getenv("SOW_SEMANTIC_COVERAGE", "1") != "0" else None
        )
        self.section_coverage_reports: dict[str, dict] = {}

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

    def _status_path(self) -> Path:
        return Path(f"data/sessions/{self.config.run_id}/status.json")

    def _mirror_status(self, step: int, message: str, progress: float) -> None:
        """C2: mirror live run status to a filesystem JSON so partial progress is
        observable across a process restart even before the durable worker (C3).
        Best-effort — a write failure never affects the run."""
        try:
            path = self._status_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(
                    {"run_id": self.config.run_id, "step": step,
                     "message": message, "progress": progress, "ts": time.time()},
                    f,
                )
            os.replace(tmp, path)
        except Exception:
            pass

    def _update_status(self, step: int, message: str, progress: float = 0.0):
        if self.status_callback:
            self.status_callback(step, message, progress)
        self._mirror_status(step, message, progress)

    def _build_or_load_tree(self, pdf_path: str) -> list[dict]:
        cache_path = Path(f"data/sessions/{self.config.run_id}/document_tree.json")
        if self.config.skip_indexing and cache_path.exists():
            logger.info(f"› loading tree from cache: {cache_path}")
            with open(cache_path) as f:
                tree = json.load(f)
            self.indexer.last_tree = tree
            return self.indexer.flatten_tree(tree)

        with console.status("[bold blue]› Building document tree via PageIndex..."):
            nodes = self.indexer.build_tree(pdf_path, status_callback=self.status_callback, stop_event=self.stop_event, run_id=self.config.run_id)

        # Cache for future skip-indexing runs
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(self.indexer, "last_tree") and self.indexer.last_tree:
            with open(cache_path, "w") as f:
                json.dump(self.indexer.last_tree, f, indent=2)

        return nodes

    def _node_concurrency(self) -> int:
        """
        Resolve the per-node extract concurrency from ``SOW_NODE_CONCURRENCY``
        (default 6). ``1`` reproduces the legacy strictly-sequential path. A
        malformed value falls back to the default rather than crashing a run.
        """
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
            section_tasks = open_tasks + newly_closed
            if self.coverage_checker is not None:
                report = self.coverage_checker.check_section(
                    node, section_text, section_tasks
                )
                # Confidence-gate the INCOMPLETE flag (Wave 3-F, audit C-4):
                # only flag when the checker is confident AND has genuine misses.
                # NOTE: the full post-dedup, run-wide INCOMPLETE restructure is a
                # later step gated by INV-4 (eval cassette asserting INCOMPLETE-rate).
                if report.missed_items:
                    self.section_coverage_reports[node["node_id"]] = report.model_dump(mode="json")
                if should_flag_section_incomplete(
                    report, self.coverage_checker.min_confidence
                ):
                    for t in section_tasks:
                        if TaskFlag.INCOMPLETE not in t.flags:
                            t.flags.append(TaskFlag.INCOMPLETE)

            return open_tasks, newly_closed, section_tasks

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

    def _write_extraction_checkpoint(self, nodes, last_index, all_closed_tasks, open_tasks, coverage) -> None:
        """
        Persist resume state after node ``last_index`` (atomic temp+replace). Holds
        the node-id list (for node-set validation on resume), the closed/open task
        sets, and coverage state. Best-effort: a checkpoint write failure must
        never abort the run — durability is a safety net, not a hard dependency.
        No-op when resumption is disabled (legacy single end-of-run checkpoint).
        """
        if not getattr(self.config, "enable_resumption", True):
            return
        try:
            path = self._extraction_checkpoint_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "node_ids": [n["node_id"] for n in nodes],
                "last_index": last_index,
                "all_closed_tasks": [t.model_dump(mode="json") for t in all_closed_tasks],
                "open_tasks": [t.model_dump(mode="json") for t in open_tasks],
                "coverage": coverage.to_dict(),
                "ts": time.time(),
            }
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(payload, f, default=str)
            os.replace(tmp, path)
        except Exception as e:
            logger.warning(f"Could not write extraction checkpoint: {e}")

    def _load_extraction_checkpoint(self) -> dict | None:
        try:
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
        """
        sync_telemetry()
        # Ensure provider_config is loaded and set ContextVar for this run/thread
        if not self.config.provider_config:
            self.config.provider_config = configure_litellm_for_mode(self.config.llm_mode)
        
        token = current_provider_config.set(self.config.provider_config)
        
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
        run_start = time.time()

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
        # Keep the payload minimal — title/depth/parent_id are enough today.
        try:
            node_index_path = Path(f"data/sessions/{self.config.run_id}/node_index.json")
            node_index_path.parent.mkdir(parents=True, exist_ok=True)
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
            with open(node_index_path, "w") as f:
                json.dump(node_index_payload, f, indent=2)
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
            if cancelled:
                return all_closed_tasks

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

        # ── Step 4: Deduplication ─────────────────────────────────────────────
        with tracer.start_as_current_span("STEP_4_DEDUP"):
            step_start = time.time()
            self._update_status(4, "Deduplicating tasks...", 0.85)
            logger.info("› Step 4/5: Deduplicating tasks...")
            deduplicated = self.dedup_agent.deduplicate(all_closed_tasks)
            logger.info(f"✓ {len(deduplicated)} tasks after deduplication")
            self.telemetry.emit("step.completed", {
                "run_id": self.config.run_id,
                "step": "deduplication",
                "duration_ms": int((time.time() - step_start) * 1000),
                "task_count": len(deduplicated),
            })

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

        # ── Assemble run health summary (GUARDRAIL-3) ─────────────────────────
        # Additive, read-only: distill the signals the stages already produced
        # (dedup degraded flag, extraction error count, coverage availability)
        # into a RunHealthReport. This does not alter any stage's behavior or the
        # value returned by run(); it only adds the `health` key to the saved
        # checkpoint and exposes self.health_report.
        self.health_report = build_health_report({
            "dedup_degraded": getattr(self.dedup_agent, "last_degraded", False),
            "dedup_degraded_reason": getattr(self.dedup_agent, "last_degraded_reason", None),
            "extraction_error_count": getattr(self.extraction_agent, "error_count", 0),
            "coverage_pct": report.get("coverage_pct"),
            # A2: capacity capping + per-node error isolation signals.
            "capacity_degraded": self.capacity_degraded,
            "capacity_degraded_reason": self.capacity_degraded_reason,
            "node_error_count": self.node_error_count,
            "node_total": len(nodes),
        })

        # ── Step 5: Save Checkpoint ───────────────────────────────────────────
        with tracer.start_as_current_span("STEP_5_SAVE"):
            step_start = time.time()
            self._update_status(5, "Saving pipeline output...", 0.95)
            logger.info("› Step 5/5: Saving pipeline output...")
            checkpoint_path = Path(f"data/sessions/{self.config.run_id}/pipeline_output.json")
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

            with open(checkpoint_path, "w") as f:
                json.dump(
                    {
                        "run_id": self.config.run_id,
                        "config": self.config.model_dump(mode="json"),
                        "tasks": [t.model_dump(mode="json") for t in deduplicated],
                        "coverage_report": report,
                        # Additive key — existing consumers ignore it.
                        "health": self.health_report.to_dict(),
                    },
                    f,
                    indent=2,
                    default=str,
                )

            # Semantic coverage reports (Improvement #2) — sibling artifact
            if self.section_coverage_reports:
                sem_path = checkpoint_path.parent / "coverage_reports.json"
                with open(sem_path, "w") as f:
                    json.dump(self.section_coverage_reports, f, indent=2, default=str)

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
        logger.info(f"Checkpoint saved: {checkpoint_path}")
        self.telemetry.emit("run.completed", {
            "run_id": self.config.run_id,
            "duration_ms": int((time.time() - run_start) * 1000),
            "task_count": len(deduplicated),
            "coverage_pct": report["coverage_pct"],
        })

        sync_telemetry()

        return deduplicated
