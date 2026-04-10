# Phase 11: Evals Architecture - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-09
**Phase:** 11-evals-architecture
**Areas discussed:** Evaluator Trigger Mechanism, LLM-as-a-Judge Configuration, Admin Container Integration

---

## Evaluator Trigger Mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| Cron/Batch Job (Recommended) | Pros: Decoupled, easy retries, simple. Cons: Scores update in batches (e.g., every 15m), not real-time. | ✓ |
| Langfuse Webhook | Pros: Real-time scoring. Cons: Requires exposing a new webhook endpoint on the admin container. | |
| Message Queue (Redis) | Pros: Highly scalable. Cons: Adds Redis as a new dependency, over-engineered for MVP. | |

**User's choice:** Cron/Batch Job (Recommended)
**Notes:** Decided to optimize for simplicity and decoupling over real-time scoring.

---

## LLM-as-a-Judge Configuration

| Option | Description | Selected |
|--------|-------------|----------|
| Admin Bifrost Proxy (Recommended) | Pros: Reuses existing credentials, isolates eval traffic from user traffic. Cons: Requires Bifrost config for the admin container. | ✓ |
| Direct API Connection | Pros: Direct connection (Langchain to API) without a proxy. Cons: Fragments credential management. | |
| User Container Proxy | Pros: One proxy rules them all. Cons: Eval traffic might compete with user extraction traffic, adding latency. | |

**User's choice:** Admin Bifrost Proxy (Recommended)
**Notes:** Decided to isolate traffic from the user container by pointing evaluations at the admin container's proxy.

---

## Admin Container Integration

| Option | Description | Selected |
|--------|-------------|----------|
| Dedicated Evaluator Container (Recommended) | Pros: Complete isolation, single responsibility (a Python container running a schedule loop). Cons: Adds one more container to the stack. | ✓ |
| Inside Existing Container | Pros: No new containers. Cons: Mixes concerns and modifies existing images/configs. | |
| Host-level Cron | Pros: Simple. Cons: Breaks Docker encapsulation (requires host setup outside compose). | |

**User's choice:** Dedicated Evaluator Container (Recommended)
**Notes:** Decided to add a new container to the `docker-compose.admin.yml` to keep concerns isolated.

---

## Claude's Discretion

None.

## Deferred Ideas

None.
