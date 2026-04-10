# Phase 11: Evals Architecture - Research

## Overview
This document summarizes the technical research required to implement offline evaluations using Langfuse and Langchain within the admin container stack, satisfying requirements EVAL-01, EVAL-02, and EVAL-03.

## Technical Approach

### 1. Evaluator Service (D-01, D-03)
- **Component**: A new dedicated Python service (`evaluator`) running within the `docker-compose.admin.yml` stack.
- **Location**: `infra/admin/evaluator/Dockerfile` and `infra/admin/evaluator/main.py`.
- **Mechanism**: A Python script utilizing a lightweight scheduler (like `schedule`) to periodically poll the Langfuse API for new, unscored traces.
- **Dependencies**: `langfuse`, `langchain`, `langchain-openai`, `schedule`, `pydantic`.

### 2. LLM-as-a-Judge Configuration (D-02)
- **Proxy Integration**: The evaluator will route its LLM calls through the existing `s2j-admin-bifrost` service.
- **Configuration**: Use `ChatOpenAI` from LangChain, setting the `openai_api_base` to `http://s2j-admin-bifrost:8081/v1` and utilizing the configured proxy credentials. This isolates evaluation API traffic from production user traffic.

### 3. Evaluation Metrics
- **Faithfulness/Precision**: LLM-as-a-judge prompt to verify the extracted Jira task does not contain hallucinations compared to the source SOW.
- **Comprehensiveness/Recall**: LLM-as-a-judge prompt to verify all deliverable items in the SOW are captured.
- **Format Adherence**: Deterministic or LLM-based check to ensure output matches the required Jira JSON schema.

### 4. Langfuse Integration
- **Ground Truth**: The evaluator will reference datasets created by `scripts/seed_full_langfuse_dataset.py`.
- **Scoring**: After evaluating a trace, the script will use the Langfuse SDK (`langfuse.score()`) to attach the precision, recall, and format adherence scores to the specific trace ID.

## Validation Architecture

To ensure the evaluator works as intended without impacting the user pipeline, we need the following verification:

1. **Dataset Integrity**: Verify `scripts/seed_full_langfuse_dataset.py` successfully populates Langfuse with the required ground-truth datasets.
2. **Evaluator Execution**: Provide a test script or command to manually trigger an evaluation run and assert that it completes and logs scores without exceptions.
3. **Score Propagation**: Assert via the Langfuse API (or a test wrapper) that scores for Faithfulness, Comprehensiveness, and Format Adherence are attached to the target trace.
4. **Isolation**: Confirm that running the evaluator does not utilize the `s2j-user-bifrost` proxy, ensuring zero impact on user container latency.

## RESEARCH COMPLETE
