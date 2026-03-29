# pipeline/orchestrator.py

import json
from pathlib import Path
from rich.progress import Progress, SpinnerColumn, TextColumn
import time

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
from audit.logger import AuditLogger
from pipeline.observability import logger, tracer, trace_span
from pipeline.telemetry import TelemetryEmitter
import os
import threading


class PipelineOrchestrator:

    def __init__(self, config: RunConfig, app_config: dict, audit: AuditLogger, status_callback=None, stop_event=None):
        self.config = config
        self.app_config = app_config
        self.audit = audit
        self.status_callback = status_callback
        self.stop_event = stop_event or threading.Event()

        # Build LLM client
        self.llm = LLMClient(
            mode=config.llm_mode,
            audit_logger=audit,
            run_id=config.run_id,
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
        self.dedup_agent = DeduplicationAgent(self.llm, audit, config.run_id, dedup_threshold)
        self.gap_agent = GapRecoveryAgent(self.llm, audit, config.run_id, max_gap_iter)

        # Configure litellm for PageIndex and build indexer
        pageindex_model = configure_litellm_for_mode(config.llm_mode)
        self.indexer = DocumentIndexer(self.app_config, model=pageindex_model)
        self.telemetry = TelemetryEmitter()

    def _update_status(self, step: int, message: str, progress: float = 0.0):
        if self.status_callback:
            self.status_callback(step, message, progress)

    def _build_or_load_tree(self, pdf_path: str) -> list[dict]:
        cache_path = Path(f"data/sessions/{self.config.run_id}/document_tree.json")
        if self.config.skip_indexing and cache_path.exists():
            logger.info(f"› --skip-indexing: loading tree from cache: {cache_path}")
            with open(cache_path) as f:
                tree = json.load(f)
            self.indexer.last_tree = tree
            return self.indexer.flatten_tree(tree)

        logger.info("› Building document tree via PageIndex")
        nodes = self.indexer.build_tree(pdf_path, status_callback=self.status_callback, stop_event=self.stop_event)

        # Cache for future skip-indexing runs
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(self.indexer, "last_tree") and self.indexer.last_tree:
            with open(cache_path, "w") as f:
                json.dump(self.indexer.last_tree, f, indent=2)

        return nodes

    @trace_span("PIPELINE_RUN", agent="Orchestrator")
    def run(self) -> list[ManagedTask]:
        """
        Full pipeline. Returns final task list ready for review UI.
        Saves checkpoint to data/pipeline_output.json after completion.
        """
        # Ensure provider_config is loaded and set ContextVar for this run/thread
        if not self.config.provider_config:
            self.config.provider_config = configure_litellm_for_mode(self.config.llm_mode)
        
        token = current_provider_config.set(self.config.provider_config)
        
        # Re-init components that need the resolved config
        self.llm.provider_config = self.config.provider_config
        self.llm.model = self.config.provider_config.model
        self.indexer.model = self.config.provider_config.model

        logger.info("═══ SOW-to-Jira Pipeline ═══")
        logger.info(f"Run ID: {self.config.run_id}")
        logger.info(f"LLM Mode: {self.config.llm_mode.value}")
        logger.info(f"Jira Hierarchy: {self.config.jira_hierarchy.value}")
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
            logger.info("› Step 1/5: Running PageIndex (parse + tree build)...")
            nodes = self._build_or_load_tree(self.config.sow_pdf_path)
            self.telemetry.emit("step.completed", {
                "run_id": self.config.run_id,
                "step": "pageindex",
                "duration_ms": int((time.time() - step_start) * 1000),
                "node_count": len(nodes),
            })

        # ── Step 2: Initialize Coverage Tracker & Safety Check ───────────────
        MAX_NODES = self.config.max_nodes
        if len(nodes) > MAX_NODES:
            error_msg = f"Denial of Wallet Protection: PDF generated {len(nodes)} sections, max allowed is {MAX_NODES}."
            logger.error(error_msg)
            raise RuntimeError(error_msg)

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
            logger.info(f"› Step 3/5: Extracting tasks from {len(nodes)} nodes...")
            all_closed_tasks: list[ManagedTask] = []
            open_tasks: list[ManagedTask] = []

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
            ) as progress:
                task_bar = progress.add_task("Processing nodes...", total=len(nodes))

                for i, node in enumerate(nodes):
                    if self.stop_event.is_set():
                        logger.warning(f"Pipeline cancelled by user at node {i}")
                        self._update_status(3, "Cancelled by user", (0.40 + (0.40 * (i/len(nodes)))))
                        return all_closed_tasks

                    node_title = node.get('title', f"Node {i}")
                    msg = f"[3/5] Processing: {node_title[:50]} ({i+1}/{len(nodes)})"
                    logger.info(msg)
                    
                    with tracer.start_as_current_span(f"PROCESS_NODE_{node['node_id']}"):
                        progress.update(task_bar, description=f"Node: {node_title[:50]}")
                        self._update_status(3, msg, 0.40 + (0.40 * (i/len(nodes))))

                        # Get text for this node (PageIndex provides it directly)
                        section_text = self.indexer.get_node_text(node)

                        # Extract (hierarchy-aware)
                        raw_tasks = self.extraction_agent.extract(
                            node, section_text, 
                            hierarchy=self.config.jira_hierarchy.value
                        )

                        # State management
                        open_tasks, newly_closed = self.state_agent.process(
                            raw_tasks, open_tasks, node
                        )

                        # Mark coverage
                        for task in open_tasks + newly_closed:
                            coverage.mark_covered(node["node_id"], str(task.id))

                        all_closed_tasks.extend(newly_closed)
                        progress.advance(task_bar)

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
                    },
                    f,
                    indent=2,
                    default=str,
                )
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

        return deduplicated
