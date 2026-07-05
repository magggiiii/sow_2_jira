"""SEAM-A: the enqueued unit-of-work seam (pipeline.jobs).

``enqueue_run`` routes a typed payload through the JobQueue port; the
``run_pipeline_job`` entrypoint is what a future worker calls. Both are tested
fully offline with a FakeQueue, a FakeProgressStore, and a STUB
orchestrator_factory — no litellm, no PipelineOrchestrator, no network.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pipeline.jobs as jobs
from pipeline.jobs import JobPayload, enqueue_run, run_pipeline_job
from integrations.queue.fake import FakeQueue
from integrations.progress.fake import FakeProgressStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PY = str(REPO_ROOT / "venv" / "bin" / "python")


def _assert_clean_in_subprocess(setup_and_check: str) -> None:
    """Run an import-cleanliness check in a FRESH interpreter.

    The pytest process accumulates ``sys.modules`` across the whole session, so
    asserting ``"litellm" not in sys.modules`` in-process is order-dependent
    (a prior test importing ui.server / the orchestrator pollutes it). Running
    the assertion in a subprocess makes it deterministic regardless of test
    order — the same approach the sibling test_observability_shims.py uses.
    """
    code = setup_and_check + "\nprint('CLEAN')\n"
    proc = subprocess.run(
        [PY, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"exit={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert "CLEAN" in proc.stdout, (
        f"stdout missing CLEAN:\n{proc.stdout}\n{proc.stderr}"
    )


def _make_payload(run_id: str = "run-abc") -> JobPayload:
    return JobPayload(
        run_id=run_id,
        kind="extraction",
        sow_pdf_path="/tmp/example.pdf",
    )


def test_enqueue_run_routes_through_port_and_records():
    # Gate 2: enqueue_run returns an id AND appends to queue.enqueued with the
    # right func_name/payload.
    q = FakeQueue()
    payload = _make_payload("run-xyz")
    job_id = enqueue_run(q, payload)

    assert isinstance(job_id, str)
    assert job_id
    assert len(q.enqueued) == 1
    rec = q.enqueued[0]
    assert rec["func_name"] == "run_pipeline_job"
    # payload serialized to a plain dict carrying the run identity.
    assert rec["payload"]["run_id"] == "run-xyz"
    assert rec["payload"]["kind"] == "extraction"
    assert rec["job_id"] == job_id


def test_run_pipeline_job_records_progress_transitions_without_litellm():
    # Gate 4: run_pipeline_job with a stub orchestrator_factory + a recording
    # ProgressStore records the FULL transition sequence (queued -> running ->
    # done), invokes the factory/run exactly once, and never imports litellm.
    calls = {"built": 0, "ran": 0}

    class _StubOrchestrator:
        def __init__(self, run_config):
            self.run_config = run_config

        def run(self):
            calls["ran"] += 1
            return {"tasks": [], "run_id": self.run_config.run_id}

    def _factory(run_config):
        calls["built"] += 1
        return _StubOrchestrator(run_config)

    class _RecordingProgressStore(FakeProgressStore):
        """FakeProgressStore that also captures the ordered set() history."""

        def __init__(self) -> None:
            super().__init__()
            self.history: list[str] = []

        def set(self, run_id, status, progress, current_step, message):
            self.history.append(status)
            super().set(run_id, status, progress, current_step, message)

    store = _RecordingProgressStore()
    payload = _make_payload("run-1")

    result = run_pipeline_job(payload, progress=store, orchestrator_factory=_factory)

    # Factory + run were invoked exactly once via the stub (no real pipeline).
    assert calls["built"] == 1
    assert calls["ran"] == 1
    assert result["run_id"] == "run-1"

    # Full transition sequence was recorded in order, not just the terminal snap.
    assert store.history == ["queued", "running", "done"]

    # Progress ended in a terminal "done" state.
    snap = store.get("run-1")
    assert snap is not None
    assert snap["status"] == "done"
    assert snap["progress"] == 1.0

    # litellm must NOT be imported as a side effect of run_pipeline_job. Asserted
    # in a fresh subprocess so it is independent of pytest's session-global
    # sys.modules (a prior test importing the orchestrator would otherwise
    # pollute the shared process and make this order-dependent).
    _assert_clean_in_subprocess(
        "import sys\n"
        "from pipeline.jobs import JobPayload, run_pipeline_job\n"
        "from integrations.progress.fake import FakeProgressStore\n"
        "class _Stub:\n"
        "    def __init__(self, rc):\n"
        "        self.rc = rc\n"
        "    def run(self):\n"
        "        return {'ok': True}\n"
        "payload = JobPayload(run_id='sub-run', kind='extraction', sow_pdf_path='/tmp/x.pdf')\n"
        "run_pipeline_job(payload, progress=FakeProgressStore(), orchestrator_factory=lambda rc: _Stub(rc))\n"
        "assert 'litellm' not in sys.modules, sorted(m for m in sys.modules if 'litellm' in m)\n"
    )


def test_run_pipeline_job_records_failed_transition_on_error():
    import pytest

    store = FakeProgressStore()
    payload = _make_payload("run-err")

    def _boom_factory(run_config):
        raise RuntimeError("stub failure")

    # The exception must propagate (re-raised) so the queue can record the
    # failure / trigger retry — swallowing it would be a contract regression.
    with pytest.raises(RuntimeError, match="stub failure"):
        run_pipeline_job(payload, progress=store, orchestrator_factory=_boom_factory)

    snap = store.get("run-err")
    assert snap is not None
    assert snap["status"] == "failed"
    assert "stub failure" in snap["message"]


def test_jobs_module_imports_clean_offline():
    # Gate 5 (import cleanliness, module level): importing pipeline.jobs must not
    # pull in litellm or pipeline.observability. Asserted in a fresh subprocess
    # so it does not depend on pytest's session-global sys.modules (a prior test
    # importing ui.server / the orchestrator pollutes the shared process).
    _assert_clean_in_subprocess(
        "import sys\n"
        "import pipeline.jobs as jobs\n"
        "assert 'litellm' not in sys.modules, sorted(m for m in sys.modules if 'litellm' in m)\n"
        "assert 'pipeline.observability' not in sys.modules, "
        "sorted(m for m in sys.modules if m == 'pipeline.observability')\n"
        "assert hasattr(jobs, 'run_pipeline_job')\n"
    )

    # The lazy job entrypoint symbol also exists in-process.
    assert hasattr(jobs, "run_pipeline_job")


def test_runkind_enum_and_runconfig_still_construct():
    # Gate 6: RunKind exists, imports alongside RunConfig, and an existing-style
    # RunConfig(...) call still constructs unchanged (RunKind is additive only).
    from models.schemas import RunConfig, RunKind, LLMMode, JiraHierarchy

    assert RunKind.EXTRACTION.value == "extraction"
    assert RunKind.PUSH.value == "push"
    # str-enum equality against the raw value (matches codebase convention).
    assert RunKind.EXTRACTION == "extraction"

    cfg = RunConfig(
        sow_pdf_path="/tmp/example.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.FLAT,
        jira_project_key="PROJ",
    )
    assert cfg.sow_pdf_path == "/tmp/example.pdf"
    assert cfg.jira_project_key == "PROJ"
