# core/agent_spec.py
"""
``AgentSpec`` — a small, flexible descriptor of ONE model call.

The six extraction-pipeline agents (extraction, critic, coverage_check,
classifier, deduplication, gap_recovery) all share the same single-call shape:

    raw = self.llm.complete_json(
        prompt=<rendered template>,
        system=<inline SYSTEM_PROMPT>,
        agent_name=<agent name>,
        node_id=<node id, sometimes omitted>,
    )

…then they parse ``raw`` (a list or dict) into their own domain objects.

``AgentSpec`` captures the *call* part of that pattern without forcing any
structure on the *parse* part. It is intentionally general: an agent that makes
multiple calls can build one spec per call, and an agent that wants to keep its
own bespoke parsing can use only ``name`` / ``system_prompt`` / ``max_tokens``
and ignore ``response_model``.

This module is dependency-free beyond ``dataclasses`` + ``typing`` (the optional
``response_model`` is a plain ``type`` — usually a Pydantic model — so no import
of pydantic is required here). It is purely additive and changes no behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

# A prompt builder is any callable that turns a payload into the final prompt
# string. The payload is whatever the agent already passes around (a dict, a
# node, a tuple of args) — kept ``Any`` so it fits every agent's call site.
PromptBuilder = Callable[[Any], str]


@dataclass
class AgentSpec:
    """
    Descriptor of a single ``complete_json`` model call.

    Fields
    ------
    name:
        Logical agent name, forwarded as ``agent_name`` to the LLM port (so the
        audit/telemetry attribution is identical to the agent calling directly).
    system_prompt:
        The inline system prompt the agent uses today. ``None`` lets the LLM
        port apply its own default (``complete_json``'s default system string).
    prompt_builder:
        Optional ``Callable[[payload], str]`` that renders the final prompt from
        the runner's ``payload`` argument. Mutually flexible with
        ``prompt_template``: provide whichever fits the agent. If both are
        ``None`` the payload is expected to already BE the rendered prompt
        string (the minimal passthrough case).
    prompt_template:
        Optional ``str.format``-style template. When set (and no
        ``prompt_builder`` is given) the runner renders it by calling
        ``template.format(**payload)`` for a mapping payload, or
        ``template.format(payload)`` otherwise.
    response_model:
        Optional type (typically a Pydantic v2 model) to validate the parsed
        JSON into. When ``None`` the parsed JSON (list/dict) is returned as-is —
        matching what the agents consume today.
    max_tokens:
        Optional override for the LLM call's ``max_tokens``. ``None`` means use
        the LLM port's own default (``complete_json`` defaults to 8192), which
        is exactly what the agents rely on today since none of them pass it.
    confidence_floor:
        Optional advisory threshold some agents use to gate
        low-confidence results. The runner does NOT enforce it (each agent's
        gating logic differs); it is carried here so a routing step can read it
        without inventing a side channel.
    """

    name: str
    system_prompt: Optional[str] = None
    prompt_builder: Optional[PromptBuilder] = None
    prompt_template: Optional[str] = None
    response_model: Optional[type] = None
    max_tokens: Optional[int] = None
    confidence_floor: Optional[float] = None

    def render(self, payload: Any) -> str:
        """
        Produce the final prompt string from ``payload``.

        Resolution order (most specific wins):
          1. ``prompt_builder(payload)`` if a builder is set.
          2. ``prompt_template`` rendered via ``str.format`` — ``**payload`` for
             a mapping, positional ``payload`` otherwise.
          3. ``payload`` itself, which must already be a string.

        Raises
        ------
        TypeError
            If no builder/template is set and ``payload`` is not a string.
        """
        if self.prompt_builder is not None:
            return self.prompt_builder(payload)

        if self.prompt_template is not None:
            if isinstance(payload, dict):
                return self.prompt_template.format(**payload)
            return self.prompt_template.format(payload)

        if isinstance(payload, str):
            return payload

        raise TypeError(
            "AgentSpec.render: no prompt_builder/prompt_template set and payload "
            f"is not a string (got {type(payload).__name__}). Provide a builder "
            "or template, or pass a pre-rendered prompt string."
        )


__all__ = ["AgentSpec", "PromptBuilder"]
