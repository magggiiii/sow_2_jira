# tests/test_intelligence_schemas.py
"""GATE D2 — intelligence_schemas consolidation.

The intelligence/eval output schemas that were defined inline in the agent
and judge modules now live in ``models.intelligence_schemas``. This test
proves:

1. Every moved class is importable from ``models.intelligence_schemas``.
2. Every ORIGINAL module still re-exports the same class objects (identity),
   so existing import sites keep resolving to the SAME class (no accidental
   fork of the type).

RED before this gate: ``models.intelligence_schemas`` is ModuleNotFound.
"""

from __future__ import annotations

import importlib


def test_intelligence_schemas_module_imports():
    mod = importlib.import_module("models.intelligence_schemas")
    for name in (
        "MissedItem",
        "SectionCoverageReport",
        "CoverageAudit",
        "CritiqueIssue",
        "TaskCritique",
        "CritiqueReport",
        "RawCritique",
        "CritiqueBatch",
        "SectionType",
        "RawClassification",
        "ClassificationResult",
        "EvaluationScores",
    ):
        assert hasattr(mod, name), f"{name} missing from intelligence_schemas"


def test_coverage_check_reexports_are_identical():
    import models.intelligence_schemas as isch
    import pipeline.agents.coverage_check as cov

    assert cov.MissedItem is isch.MissedItem
    assert cov.SectionCoverageReport is isch.SectionCoverageReport
    assert cov.CoverageAudit is isch.CoverageAudit


def test_critic_reexports_are_identical():
    import models.intelligence_schemas as isch
    import pipeline.agents.critic as critic

    assert critic.CritiqueIssue is isch.CritiqueIssue
    assert critic.TaskCritique is isch.TaskCritique
    assert critic.CritiqueReport is isch.CritiqueReport
    assert critic.RawCritique is isch.RawCritique
    assert critic.CritiqueBatch is isch.CritiqueBatch


def test_classifier_reexports_are_identical():
    import models.intelligence_schemas as isch
    import pipeline.agents.classifier as clf

    assert clf.SectionType is isch.SectionType
    assert clf.RawClassification is isch.RawClassification
    assert clf.ClassificationResult is isch.ClassificationResult


def test_judges_reexports_are_identical():
    import models.intelligence_schemas as isch
    import pipeline.evals.judges as judges

    assert judges.EvaluationScores is isch.EvaluationScores


def test_moved_models_still_behave():
    # A quick behavioral smoke: the moved models validate and clamp as before.
    from models.intelligence_schemas import (
        ClassificationResult,
        MissedItem,
        SectionType,
    )

    item = MissedItem(description="dropped export", confidence=1.5, reason="r")
    assert item.confidence == 1.0  # CONF-1 clamp preserved

    result = ClassificationResult(
        node_id="n1", type=SectionType.ACTIONABLE, confidence=-0.3, reason="r"
    )
    assert result.confidence == 0.0
    assert result.type == SectionType.ACTIONABLE
