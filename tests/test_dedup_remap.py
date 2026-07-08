# tests/test_dedup_remap.py
"""Replay-time dedup-UUID remapper (STEP 3.8, offline sub-component).

A recorded DeduplicationAgent response embeds the RECORDING run's fresh task
UUIDs in task_id_a/task_id_b. A replay run mints DIFFERENT UUIDs, so a statically
recorded dedup decision won't match the replay run's tasks. The remapper rewrites
those recorded ids onto the CURRENT run's task ids, matching each recorded task
identity by title -> source node_id -> position. Tested against SYNTHETIC
recorded cassettes (no live model run).
"""

from __future__ import annotations

from models.schemas import ManagedTask, SourceRef
from pipeline.evals.replay import (
    RecordedTaskRef,
    build_dedup_id_remap,
    remap_recorded_dedup_ids,
)


def _task(title: str, node_id: str) -> ManagedTask:
    return ManagedTask(
        title=title,
        short_description="d",
        confidence=0.9,
        source_refs=[
            SourceRef(node_id=node_id, section_title="s", page_start=1, page_end=1)
        ],
    )


def _decision(a: str, b: str, decision: str = "merge", reason: str = "dup") -> dict:
    return {"task_id_a": a, "task_id_b": b, "decision": decision, "reason": reason}


# ── Happy path: title match ───────────────────────────────────────────────────


def test_title_match_remaps_recorded_ids_onto_current_uuids():
    current = [_task("Build Auth", "n1"), _task("Build Login", "n2")]
    refs = [
        RecordedTaskRef(id="old-A", title="Build Auth", node_id="n1", position=0),
        RecordedTaskRef(id="old-B", title="Build Login", node_id="n2", position=1),
    ]
    recorded = {"decisions": [_decision("old-A", "old-B")]}

    out = remap_recorded_dedup_ids(recorded, refs, current)

    d = out["decisions"][0]
    assert d["task_id_a"] == str(current[0].id)
    assert d["task_id_b"] == str(current[1].id)
    # non-id fields preserved verbatim
    assert d["decision"] == "merge"
    assert d["reason"] == "dup"


def test_remapped_ids_reference_the_current_run_tasks():
    # The whole point: after remap, the recorded decision points at THIS run's
    # tasks (so a recorded merge is actually applicable on replay).
    current = [_task("Build Auth", "n1"), _task("Build Login", "n2")]
    refs = [
        RecordedTaskRef(id="old-A", title="Build Auth", node_id="n1", position=0),
        RecordedTaskRef(id="old-B", title="Build Login", node_id="n2", position=1),
    ]
    recorded = {"decisions": [_decision("old-A", "old-B")]}

    out = remap_recorded_dedup_ids(recorded, refs, current)

    current_ids = {str(t.id) for t in current}
    for d in out["decisions"]:
        assert d["task_id_a"] in current_ids
        assert d["task_id_b"] in current_ids


# ── Fallbacks ─────────────────────────────────────────────────────────────────


def test_node_id_fallback_when_titles_are_ambiguous():
    # Two current tasks share a title -> title alone can't disambiguate; the
    # recorded node_id resolves it.
    current = [_task("Task", "n1"), _task("Task", "n2")]
    refs = [RecordedTaskRef(id="old-A", title="Task", node_id="n2", position=0)]

    remap = build_dedup_id_remap(refs, current)

    assert remap["old-A"] == str(current[1].id)  # matched by node n2, not title/position


def test_populated_but_missed_anchors_are_not_positionally_matched():
    # A ref that HAS a title AND node_id which both MISS the current run means the
    # recorded task vanished (extraction drift). Blindly using position would
    # merge an unrelated present task, so the ref stays UNMAPPED (its decision is
    # dropped) — position is not a valid signal once real anchors were checked.
    current = [_task("X", "nx"), _task("Y", "ny")]
    refs = [RecordedTaskRef(id="old-A", title="Vanished", node_id="zzz", position=1)]

    remap = build_dedup_id_remap(refs, current)

    assert "old-A" not in remap


def test_position_fallback_only_for_anchorless_ref():
    # When a ref carries NO identity anchors (title="" and node_id=""), position
    # is the only available signal and IS used as a last resort.
    current = [_task("X", "nx"), _task("Y", "ny")]
    refs = [RecordedTaskRef(id="old-A", title="", node_id="", position=1)]

    remap = build_dedup_id_remap(refs, current)

    assert remap["old-A"] == str(current[1].id)


def test_anchored_ref_is_not_preempted_by_earlier_positional_ref():
    # Claiming is global, but a positional (anchor-less) ref listed FIRST must not
    # steal a task that a later anchored ref rightfully owns by title/node —
    # strong (identity) matches take precedence over weak (positional) ones.
    current = [_task("Alpha", "na")]
    refs = [
        RecordedTaskRef(id="weak", title="", node_id="", position=0),
        RecordedTaskRef(id="strong", title="Alpha", node_id="na", position=9),
    ]

    remap = build_dedup_id_remap(refs, current)

    assert remap.get("strong") == str(current[0].id)  # anchored owner wins the task
    assert "weak" not in remap  # positional ref yields to the anchored one


def test_title_beats_node_and_position():
    # title, node_id, and position each point at a DIFFERENT current task; title
    # (the strongest signal) must win. Isolates the title branch — with it
    # disabled the ref would resolve to the node_id/position target instead.
    current = [_task("Alpha", "na"), _task("Beta", "nb")]
    refs = [RecordedTaskRef(id="old-A", title="Alpha", node_id="nb", position=1)]

    remap = build_dedup_id_remap(refs, current)

    assert remap["old-A"] == str(current[0].id)  # title(idx0) beats node_id(idx1) + position(idx1)


def test_title_match_is_case_and_whitespace_insensitive():
    current = [_task("Build Auth", "n1")]
    refs = [RecordedTaskRef(id="old-A", title="  build auth ", node_id="zzz", position=9)]

    remap = build_dedup_id_remap(refs, current)

    assert remap["old-A"] == str(current[0].id)


# ── Unmappable decisions are dropped, not faked ──────────────────────────────


def test_decision_with_unmappable_endpoint_is_dropped():
    current = [_task("Only", "n1")]
    refs = [RecordedTaskRef(id="old-A", title="Only", node_id="n1", position=0)]
    recorded = {"decisions": [_decision("old-A", "old-ORPHAN")]}

    out = remap_recorded_dedup_ids(recorded, refs, current)

    assert out["decisions"] == []  # old-ORPHAN can't be remapped -> whole decision dropped


def test_collision_two_refs_onto_one_current_task_drops_self_merge():
    # Two DISTINCT recorded tasks whose anchors both resolve to the SAME single
    # current task must NOT collapse into a self-merge (task_id_a == task_id_b).
    # On replay a self-merge deletes the real task, so the decision is dropped
    # and the two recorded ids never alias onto the same current id.
    current = [_task("Solo", "n1")]
    refs = [
        RecordedTaskRef(id="old-A", title="Solo", node_id="n1", position=0),
        RecordedTaskRef(id="old-B", title="Solo", node_id="n1", position=0),
    ]
    recorded = {"decisions": [_decision("old-A", "old-B")]}

    out = remap_recorded_dedup_ids(recorded, refs, current)
    assert out["decisions"] == []

    remap = build_dedup_id_remap(refs, current)
    assert len(set(remap.values())) == len(remap)  # no two old ids share a current id


def test_recorded_self_referential_decision_is_dropped():
    # A degenerate recorded decision that already names one task twice must not
    # survive remap (it would become a self-merge).
    current = [_task("Solo", "n1")]
    refs = [RecordedTaskRef(id="old-A", title="Solo", node_id="n1", position=0)]
    recorded = {"decisions": [_decision("old-A", "old-A")]}

    out = remap_recorded_dedup_ids(recorded, refs, current)
    assert out["decisions"] == []


def test_multiple_decisions_preserve_order_and_drop_only_the_unmappable():
    current = [_task("A", "na"), _task("B", "nb"), _task("C", "nc")]
    refs = [
        RecordedTaskRef(id="oa", title="A", node_id="na", position=0),
        RecordedTaskRef(id="ob", title="B", node_id="nb", position=1),
        RecordedTaskRef(id="oc", title="C", node_id="nc", position=2),
    ]
    recorded = {
        "decisions": [
            _decision("oa", "ob", reason="first"),
            _decision("oa", "zzz", reason="dropme"),
            _decision("ob", "oc", reason="third"),
        ]
    }

    out = remap_recorded_dedup_ids(recorded, refs, current)

    assert [d["reason"] for d in out["decisions"]] == ["first", "third"]
    assert out["decisions"][0]["task_id_a"] == str(current[0].id)
    assert out["decisions"][1]["task_id_b"] == str(current[2].id)


# ── Round-trip: remapped dict validates into the real model ──────────────────


def test_remapped_dict_validates_into_dedup_decision_list_with_current_ids():
    from pipeline.agents.deduplication import DedupDecisionList

    current = [_task("Build Auth", "n1"), _task("Build Login", "n2")]
    refs = [
        RecordedTaskRef(id="old-A", title="Build Auth", node_id="n1", position=0),
        RecordedTaskRef(id="old-B", title="Build Login", node_id="n2", position=1),
    ]
    recorded = {"decisions": [_decision("old-A", "old-B")]}

    out = remap_recorded_dedup_ids(recorded, refs, current)
    parsed = DedupDecisionList.model_validate(out)

    assert len(parsed.decisions) == 1
    assert parsed.decisions[0].task_id_a == str(current[0].id)
    assert parsed.decisions[0].task_id_b == str(current[1].id)


def test_empty_and_missing_decisions_are_handled():
    current = [_task("A", "na")]
    refs = [RecordedTaskRef(id="oa", title="A", node_id="na", position=0)]

    assert remap_recorded_dedup_ids({"decisions": []}, refs, current)["decisions"] == []
    # a recorded blob missing the key entirely -> empty decisions, no crash
    assert remap_recorded_dedup_ids({}, refs, current)["decisions"] == []
