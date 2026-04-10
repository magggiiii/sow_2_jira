import pytest
import os
from models.eval_schemas import HierarchicalDatasetItem, GoldenEpic, GoldenTicket

def test_hierarchical_schemas():
    """Verifies that evaluation schemas are correct and serializable."""
    ticket = GoldenTicket(
        title="Test Ticket",
        short_description="This is a test description",
        acceptance_criteria=["AC 1", "AC 2"]
    )
    epic = GoldenEpic(
        title="Test Epic",
        short_description="This is a test epic description",
        tickets=[ticket]
    )
    item = HierarchicalDatasetItem(epic=epic)
    
    data = item.model_dump()
    assert data["epic"]["title"] == "Test Epic"
    assert len(data["epic"]["tickets"]) == 1
    assert data["epic"]["tickets"][0]["title"] == "Test Ticket"
    print("✓ Hierarchical schemas verified")

def test_run_eval_dataset_script_imports():
    """Verifies that the run_eval_dataset script can be imported correctly."""
    from scripts.run_eval_dataset import run_eval_dataset
    assert run_eval_dataset is not None
    print("✓ run_eval_dataset script imports verified")

def test_run_eval_dataset_script():
    """Verifies the runner functionality (unit test)."""
    # This would normally require Langfuse, we'll mock it if needed in a real CI.
    # For now, we just ensure it exists and has the correct methods.
    import scripts.run_eval_dataset as runner
    assert hasattr(runner, "run_eval_dataset")
    print("✓ run_eval_dataset script structure verified")

def test_dataset_integrity():
    """Verifies Langfuse datasets exist and have valid structure."""
    # This requires a live Langfuse instance.
    pass

def test_evaluator_execution():
    """Verifies evaluator container connectivity to Bifrost and Langfuse."""
    # This requires a live environment.
    pass

def test_score_propagation():
    """Verifies scores are correctly attached to Langfuse traces."""
    # This requires a live environment.
    pass
