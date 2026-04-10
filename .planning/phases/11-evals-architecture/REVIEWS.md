# Phase 11: Evals Architecture - Re-planning Feedback

## User Requirements
- **Hierarchy-First**: Evaluation must respect the `EPIC -> tickets` hierarchy established in the extraction engine.
- **Golden Dataset**: Focus on creating a database of "perfect" extractions (the Golden Set) from a full SOW.
- **Comparison Logic**: The core evaluation should compare the system's generated response (EPIC -> tickets) against the golden dataset.
- **LLM-as-a-Judge**: Use this for the comparison if it's the most effective approach. 
- **Model Preference**: Use local models for evaluation where possible; use API models as a fallback.
- **Deferred**: Fully automated background scoring service can be simplified/deferred in favor of establishing the correct dataset and comparison structure first.

## Strategy Shift
Instead of a generic background scorer, the evaluator should focus on:
1.  **Dataset Definition**: Defining how a "Golden Extraction" is stored in Langfuse (e.g., as a Dataset with input SOW and expected JSON output).
2.  **Run logic**: A script to run the extraction engine against the dataset and link results as "Runs".
3.  **Custom Evaluator**: A Langchain-based evaluator that understands the `EPIC -> tickets` structure and scores based on coverage, hallucination, and hierarchical accuracy.
