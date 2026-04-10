import pytest
from unittest.mock import MagicMock
from pipeline.evals.judges import HierarchicalJudge, EvaluationScores

class MockResponse:
    def __init__(self, content):
        self.content = content

def test_judge_parsing():
    """Verifies that the judge can parse a JSON response from an LLM."""
    judge = HierarchicalJudge()
    
    # Mock LLM response directly on the instance
    # We need to mock 'invoke' on self.llm
    judge.llm = MagicMock()
    judge.llm.invoke.return_value = MockResponse(
        '```json {"alignment": 1.0, "recall": 0.8, "fidelity": 0.9, "hallucination": 0.0, "reasoning": "Excellent match"} ```'
    )
    
    # We also need to mock the prompt/chain since it's used as (self.prompt | self.llm).invoke()
    # Or just mock the whole chain.invoke in evaluate
    
    scores = judge.evaluate("Source", {"expected": "truth"}, [{"actual": "test"}])
    
    assert scores.alignment == 1.0
    assert scores.recall == 0.8
    assert scores.fidelity == 0.9
    assert scores.hallucination == 0.0
    assert scores.reasoning == "Excellent match"
    print("✓ Judge parsing verified")

def test_judge_error_handling():
    """Verifies that the judge handles malformed LLM responses gracefully."""
    judge = HierarchicalJudge()
    judge.llm = MagicMock()
    judge.llm.invoke.return_value = MockResponse("Invalid non-JSON response")
    
    scores = judge.evaluate("Source", {"expected": "truth"}, [{"actual": "test"}])
    
    assert scores.alignment == 0
    assert "Error parsing" in scores.reasoning
    print("✓ Judge error handling verified")
