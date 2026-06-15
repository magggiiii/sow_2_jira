# pipeline/agents/extraction.py

import json
from pydantic import BaseModel, Field

from models.schemas import RawTask, TaskFlag, normalize_acceptance_criteria
from pipeline.llm_client import LLMClient
from audit.logger import AuditLogger
from core.agent_runner import AgentRunner, InstructorError


class ExtractionResult(BaseModel):
    """Instructor ``response_model`` (C-5) — the extractor's output object.

    Mirrors the ``{"scratchpad": ..., "tasks": [...]}`` wrapper the prompt asks
    for. ``tasks`` is a list of the existing :class:`RawTask` (validated, with
    its permissive Union ACs and clamped confidence), so the per-task dict-parse
    loop the regex path needed is replaced by iterating already-validated tasks.
    The legacy bare-array shape the agent used to special-case is now Instructor's
    concern: the runner always hands back a validated ExtractionResult.
    """
    scratchpad: str = ""
    tasks: list[RawTask] = Field(default_factory=list)

EXTRACTION_SYSTEM_PROMPT = """You are a senior Jira project manager extracting actionable work items from a Statement of Work (SOW).
You think in terms of real Jira boards: Epics, Stories, Tasks, and Sub-tasks.
Return ONLY valid JSON. No explanation. No markdown fences. No preamble."""

EXTRACTION_PROMPT_TEMPLATE = """You are extracting actionable Jira tickets from a Statement of Work (SOW) section.

═══ TASK GRANULARITY ═══
- Each task MUST be a single, atomic unit of work completable by one person in 1-2 sprints (2-4 weeks).
- If a section describes a large system or module, DECOMPOSE it into multiple focused tasks.
- NEVER create a single task like "Implement the entire reporting module" — break it down.
- Think: "Could a developer pick this up on Monday and demo it in the sprint review?"

═══ TITLE FORMAT ═══
- MUST start with an action verb: Create, Implement, Design, Configure, Integrate, Build, Set up, Develop, Define, Write
- MUST follow: "[Verb] [specific object] [optional context]"
- Max 80 characters
- GOOD: "Create user authentication API with JWT tokens"
- GOOD: "Design database schema for order management"  
- GOOD: "Integrate payment gateway with Stripe API"
- BAD: "User Authentication" (no verb, too vague)
- BAD: "The system should handle payments" (not actionable)
- BAD: "Implement the entire backend system" (too broad)

═══ ACCEPTANCE CRITERIA ═══
- MUST be testable, measurable conditions — not descriptions of features.
- PREFERRED (structured) format per item:
    {{"condition": "API returns 200 OK with user profile JSON when valid token is provided",
      "type": "functional", "verified_by": "test"}}
  Allowed type values: "functional", "nonfunctional", "security", "performance", "usability".
  Allowed verified_by values: "test", "review", "demo", "inspection".
- LEGACY (still accepted) format per item: a plain string like "[ ] API returns 200 OK..."
- GOOD: "[ ] Dashboard loads within 3 seconds on 4G connection" (type: performance)
- GOOD: "[ ] Error message is displayed when invalid email format is entered" (type: usability)
- BAD: "The system works correctly" (not testable)
- BAD: "Users can log in" (too vague — HOW do we verify?)
- If you cannot determine testable criteria from the text, set to null and add "NO_ACCEPTANCE_CRITERIA" to flags.

═══ DEPENDENCIES ═══
- Optional. List sibling-task references that must complete before this one.
- target_ref MUST match another task's TITLE within the same SOW (best-effort string match).
- reason: one short clause explaining WHY this blocks.
- kind: "blocks" (default), "relates_to", or "duplicates".
- Omit the field entirely if no dependencies are obvious.

═══ WHAT TO EXTRACT ═══
- Functional requirements → development tasks
- Integration points → integration tasks
- Data migration needs → migration tasks
- Configuration/setup work → setup tasks
- Testing requirements explicitly mentioned → testing tasks

═══ WHAT TO SKIP ═══
- Background context, company descriptions, project overviews
- Legal terms, payment terms, warranties, confidentiality clauses
- Definitions, glossary items, acronyms
- General assumptions (unless they imply work)
- Signatures, approval sections
- If the ENTIRE section is non-actionable, return an empty array []

═══ FLAGS ═══
- NO_ACCEPTANCE_CRITERIA: Cannot determine testable acceptance criteria
- AMBIGUOUS_SCOPE: The scope is vague, contradictory, or could be interpreted multiple ways
- INCOMPLETE: Key information is missing (e.g., mentions an API but not what it should do)
- LOW_CONFIDENCE: You are less than 60% sure this is a real task

═══ OUTPUT FORMAT ═══
Return a SINGLE JSON object with two top-level fields:
- "scratchpad": a short string (max ~80 words) where you think out loud before listing tasks. Use it to identify the atomic units of work, note risks, and check yourself against the rules above. This field is for audit only — keep it terse.
- "tasks": an array of task objects. Each object must have EXACTLY these fields:

{{
  "title": "string — verb-first, max 80 chars",
  "short_description": "string — 1-2 sentences, what this task delivers",
  "acceptance_criteria": [
    {{"condition": "testable statement", "type": "functional", "verified_by": "test"}},
    "[ ] legacy plain-string form is also accepted"
  ] or null,
  "use_case": "string — As a [role], I want [goal] so that [benefit]" or null,
  "considerations_constraints": ["string", ...] or null,
  "deliverables": ["string — concrete output", ...] or null,
  "mockup_prototype": "string — reference to mockup/prototype" or null,
  "confidence": 0.0 to 1.0,
  "flags": ["FLAG_NAME", ...],
  "continues_to_next": true or false,
  "dependencies": [
    {{"target_ref": "Title of the task that must finish first",
      "reason": "Why this blocks", "kind": "blocks"}}
  ]
}}

If the section is non-actionable, return {{"scratchpad": "...", "tasks": []}}.
Return ONLY the JSON object. No preamble. No explanation. No markdown.

═══ EXAMPLES ═══
The following examples show GOOD extractions on realistic SOW phrasings.
Mirror their granularity, verb-first titles, and structured ACs.

Example 1 — Feature work
Section snippet:
"The platform shall provide user authentication. Users must be able to register with email and password, log in, and reset forgotten passwords via an emailed link. Two-factor authentication via TOTP is required for admin accounts."

Output:
{{"scratchpad": "Auth + 2FA + password reset = 3 atomic tasks. 2FA only for admins, capture in AC.", "tasks": [
  {{"title": "Implement email/password registration and login API",
    "short_description": "Build the public auth endpoints that issue session tokens on valid credentials.",
    "acceptance_criteria": [
      {{"condition": "POST /auth/register creates a user with hashed password and returns 201", "type": "functional", "verified_by": "test"}},
      {{"condition": "POST /auth/login returns a signed session token on valid credentials and 401 otherwise", "type": "security", "verified_by": "test"}}
    ],
    "use_case": "As a new user, I want to register and log in so that I can access the platform.",
    "considerations_constraints": ["Passwords must be bcrypt-hashed", "Session tokens must expire"],
    "deliverables": ["/auth/register endpoint", "/auth/login endpoint"],
    "mockup_prototype": null, "confidence": 0.9, "flags": [], "continues_to_next": false, "dependencies": []}},
  {{"title": "Implement password reset via emailed one-time link",
    "short_description": "Allow users to request a password reset email and complete the reset via a tokenized link.",
    "acceptance_criteria": [
      {{"condition": "Reset link is single-use and expires within 30 minutes", "type": "security", "verified_by": "test"}},
      {{"condition": "POST /auth/reset accepts the link token and a new password and updates the user record", "type": "functional", "verified_by": "test"}}
    ],
    "use_case": null, "considerations_constraints": null, "deliverables": ["/auth/reset-request endpoint", "/auth/reset endpoint"],
    "mockup_prototype": null, "confidence": 0.85, "flags": [], "continues_to_next": false,
    "dependencies": [{{"target_ref": "Implement email/password registration and login API", "reason": "Reset acts on the user record from registration", "kind": "blocks"}}]}},
  {{"title": "Add TOTP-based 2FA enforcement for admin accounts",
    "short_description": "Enroll admins in TOTP and require a valid code on login.",
    "acceptance_criteria": [
      {{"condition": "Admin login requires a valid 6-digit TOTP code in addition to password", "type": "security", "verified_by": "test"}}
    ],
    "use_case": null, "considerations_constraints": ["Only enforced when the user has role=admin"], "deliverables": ["TOTP enrollment flow", "Login verification step"],
    "mockup_prototype": null, "confidence": 0.85, "flags": [], "continues_to_next": false,
    "dependencies": [{{"target_ref": "Implement email/password registration and login API", "reason": "2FA layers on top of password login", "kind": "blocks"}}]}}
]}}

Example 2 — Integration
Section snippet:
"The order service must integrate with the Stripe payment gateway. The integration must support card payments, webhook receipt for payment events, and refunds initiated from the admin console."

Output:
{{"scratchpad": "Two atomic units: outbound Stripe charge/refund client, and inbound webhook receiver. Admin refund UI is separate from this section.", "tasks": [
  {{"title": "Integrate Stripe payment client for card charges and refunds",
    "short_description": "Wrap the Stripe SDK with a typed client used by the order service for charges and refunds.",
    "acceptance_criteria": [
      {{"condition": "client.charge(amount, card_token) returns a Stripe payment_intent_id on success", "type": "functional", "verified_by": "test"}},
      {{"condition": "client.refund(payment_intent_id) returns 200 and marks the order refunded in the local DB", "type": "functional", "verified_by": "test"}}
    ],
    "use_case": null, "considerations_constraints": ["Use Stripe test keys in non-prod", "Retries on 5xx are bounded"],
    "deliverables": ["StripeClient module", "Charge/refund unit tests"], "mockup_prototype": null,
    "confidence": 0.9, "flags": [], "continues_to_next": false, "dependencies": []}},
  {{"title": "Build Stripe webhook receiver for payment events",
    "short_description": "Expose a signed-webhook endpoint that updates order status on payment_intent.succeeded and charge.refunded.",
    "acceptance_criteria": [
      {{"condition": "POST /webhooks/stripe rejects requests with an invalid Stripe-Signature header", "type": "security", "verified_by": "test"}},
      {{"condition": "On payment_intent.succeeded the matching order transitions to PAID", "type": "functional", "verified_by": "test"}}
    ],
    "use_case": null, "considerations_constraints": ["Idempotent on event_id"], "deliverables": ["/webhooks/stripe endpoint"],
    "mockup_prototype": null, "confidence": 0.85, "flags": [], "continues_to_next": false,
    "dependencies": [{{"target_ref": "Integrate Stripe payment client for card charges and refunds", "reason": "Webhook updates orders charged via the client", "kind": "relates_to"}}]}}
]}}

Example 3 — Data migration
Section snippet:
"Existing customer data currently stored in the legacy MySQL Customers table must be migrated to the new PostgreSQL customers schema. Email addresses must be lower-cased on migration and historical orders must remain linked via customer_id."

Output:
{{"scratchpad": "One migration task with two clear ACs: lowercase emails and preserve order linkage. Confidence high — concrete source/target named.", "tasks": [
  {{"title": "Migrate legacy MySQL Customers table into PostgreSQL customers schema",
    "short_description": "One-time migration that copies all rows, normalizes email casing, and preserves order foreign keys.",
    "acceptance_criteria": [
      {{"condition": "All emails in postgres.customers.email are stored lower-cased", "type": "functional", "verified_by": "inspection"}},
      {{"condition": "orders.customer_id resolves to the matching postgres.customers.id for 100% of historical orders", "type": "functional", "verified_by": "test"}}
    ],
    "use_case": null, "considerations_constraints": ["Run in a maintenance window", "Migration must be idempotent"],
    "deliverables": ["migrate_customers.py script", "Post-migration verification report"],
    "mockup_prototype": null, "confidence": 0.9, "flags": [], "continues_to_next": false, "dependencies": []}}
]}}

{hierarchy_context}
SOW Section Title: {section_title}
SOW Pages: {page_start} to {page_end}

Section Text:
{section_text}
"""

HIERARCHY_CONTEXT = {
    "flat": """═══ HIERARCHY CONTEXT ═══
Target: FLAT (standalone Tasks, no parent).
Extract medium-grained, self-contained tasks. Each task should make sense on its own without parent context.
""",
    "epic_task": """═══ HIERARCHY CONTEXT ═══
Target: EPIC → TASK hierarchy.
This SOW section will become an Epic. Extract atomic Tasks that belong under it.
Each task should be completable by one developer in 1-2 sprints.
Do not duplicate the section's high-level goal — focus on concrete implementation work.
""",
    "story_subtask": """═══ HIERARCHY CONTEXT ═══
Target: STORY → SUB-TASK hierarchy.
This SOW section will become a Story. Extract fine-grained Sub-tasks.
Break work down into the smallest meaningful units (a few hours to a few days each).
Multiple sub-tasks per feature area is expected. Be specific and granular.
""",
}


class TaskExtractionAgent:

    def __init__(self, llm_client: LLMClient, audit_logger: AuditLogger,
                 run_id: str, confidence_threshold: float = 0.6, max_section_chars: int = 16000):
        self.llm = llm_client
        # Route the single LLM call through the AgentRunner seam. The runner's
        # complete_json is a thin passthrough over the same LLMProvider, so the
        # request, return value, and propagated exceptions are unchanged.
        self.runner = AgentRunner(llm_client)
        self.audit = audit_logger
        self.run_id = run_id
        self.confidence_threshold = confidence_threshold
        self.max_section_chars = max_section_chars
        # GUARDRAIL-3: additive, read-only counter of recoverable extraction
        # errors across this run. Incremented on the existing error paths only;
        # it never changes control flow or return values. The orchestrator reads
        # it to derive an extraction StageHealth for the RunHealthReport.
        self.error_count = 0

    def extract(self, node: dict, section_text: str, hierarchy: str = "epic_task", status_callback=None) -> list[RawTask]:
        """
        Runs extraction on a single PageIndex node.
        Returns a list of RawTask objects.
        hierarchy: one of 'flat', 'epic_task', 'story_subtask' — adjusts extraction granularity.
        Auto-adds LOW_CONFIDENCE flag to tasks below threshold.
        Returns empty list if section_text is too short (< 50 chars).
        """
        # Set the callback on the LLM client temporarily for this call if provided
        if status_callback and hasattr(self.llm, "status_callback"):
            self.llm.status_callback = status_callback
        if len(section_text.strip()) < 50:
            self.audit.log(
                run_id=self.run_id,
                agent="ExtractionAgent",
                node_id=node["node_id"],
                action="SKIPPED_SHORT_SECTION",
                detail=f"Section '{node['title']}' too short to extract",
            )
            return []

        if len(section_text) > self.max_section_chars:
            section_text = section_text[:self.max_section_chars]
            truncation_notice = (
                f"\n\n[NOTE: This section was truncated at {self.max_section_chars} characters. "
                f"Review the original SOW section '{node['title']}' pages "
                f"{node['page_start']}-{node['page_end']} for any tasks not captured here.]"
            )
            section_text += truncation_notice

        prompt = EXTRACTION_PROMPT_TEMPLATE.format(
            section_title=node["title"],
            page_start=node["page_start"],
            page_end=node["page_end"],
            section_text=section_text,
            hierarchy_context=HIERARCHY_CONTEXT.get(hierarchy, HIERARCHY_CONTEXT["epic_task"]),
        )

        # C-5: route through the Instructor-validated structured-output seam.
        # The runner returns a validated ExtractionResult — the {scratchpad,
        # tasks} wrapper, with each task already a schema-valid RawTask. That
        # replaces the manual dict/list shape juggling and the per-task
        # RawTask(**raw) parse loop. Any structured-output failure (call error,
        # unparseable output, a tasks field Instructor could not coerce into a
        # list of RawTask) surfaces as one InstructorError, recorded as
        # EXTRACTION_ERROR (error_count++) and degraded to an empty list.
        try:
            result = self.runner.complete_structured(
                prompt=prompt,
                response_model=ExtractionResult,
                system=EXTRACTION_SYSTEM_PROMPT,
                agent_name="ExtractionAgent",
                node_id=node["node_id"],
            )
        except InstructorError as e:
            self.error_count += 1
            self.audit.log(
                run_id=self.run_id,
                agent="ExtractionAgent",
                node_id=node["node_id"],
                action="EXTRACTION_ERROR",
                detail=str(e),
            )
            return []

        # Audit-log the scratchpad so reviewers can see what the model was
        # thinking, but never propagate it into RawTask.
        scratchpad = result.scratchpad or ""
        if scratchpad:
            self.audit.log(
                run_id=self.run_id,
                agent="ExtractionAgent",
                node_id=node["node_id"],
                action="EXTRACTION_SCRATCHPAD",
                detail=scratchpad[:1000],
            )

        tasks = []
        for task in result.tasks:
            # `task` is already a validated RawTask. Apply the same downstream
            # touches the regex path did: auto-flag low confidence, then
            # normalize ACs to the structured form so the rest of the pipeline
            # only deals with AcceptanceCriterion objects.
            if task.confidence < self.confidence_threshold:
                if "LOW_CONFIDENCE" not in task.flags:
                    task.flags.append("LOW_CONFIDENCE")
            task.acceptance_criteria = normalize_acceptance_criteria(
                task.acceptance_criteria
            )
            tasks.append(task)

        self.audit.log(
            run_id=self.run_id,
            agent="ExtractionAgent",
            node_id=node["node_id"],
            action="EXTRACTED",
            detail=f"Extracted {len(tasks)} tasks from section '{node['title']}'",
        )

        return tasks