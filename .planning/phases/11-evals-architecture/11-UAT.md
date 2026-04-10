---
status: testing
phase: 11-evals-architecture
source: [11-01-SUMMARY.md, 11-02-SUMMARY.md, 11-03-SUMMARY.md]
started: 2026-04-09T10:30:00Z
updated: 2026-04-09T10:30:00Z
---

## Current Test

number: 1
name: Hierarchical Dataset Item Schema
expected: |
  User can define a HierarchicalDatasetItem containing a GoldenEpic and multiple GoldenTickets using the new models.
awaiting: user response

## Tests

### 1. Hierarchical Dataset Item Schema
expected: User can define a HierarchicalDatasetItem containing a GoldenEpic and multiple GoldenTickets using the new models.
result: [pending]

### 2. Hierarchical Seeding Script (Dry Run)
expected: The script scripts/seed_hierarchical_eval_dataset.py can be executed and processes SOW features into logical Epics.
result: [pending]

### 3. Hierarchical Judge Logic
expected: The judge in pipeline/evals/judges.py can compare an actual extraction against a golden expected output and return scores for Alignment, Recall, Fidelity, and Hallucination.
result: [pending]

### 4. Admin Bifrost Configuration
expected: config/admin/bifrost.admin.yaml contains the ollama-local provider and evaluator virtual key.
result: [pending]

### 5. Evaluator Service Infrastructure
expected: infra/admin/evaluator/Dockerfile and docker-compose.admin.yml correctly define the background service with isolated networking.
result: [pending]

## Summary

total: 5
passed: 0
issues: 0
pending: 5
skipped: 0
blocked: 0

## Gaps

<!-- YAML format for plan-phase --gaps consumption -->
