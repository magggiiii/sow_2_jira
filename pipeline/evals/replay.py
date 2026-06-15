# pipeline/evals/replay.py
"""
Offline replay shims for the eval harness.

``NoOpProvider`` satisfies the ``core.ports.LLMProvider`` port so it can fill the
``PipelineOrchestrator(llm=...)`` slot without constructing a real ``LLMClient``
(no network at orchestrator build time). It is never actually consulted for
structured output — agents reach structured output through
``AgentRunner.complete_structured``, which the harness patches with
``make_structured_replay`` to serve recorded responses from a :class:`Cassette`.

``make_structured_replay`` returns a function with the exact signature of
``AgentRunner.complete_structured`` (``self`` first, then keyword-only args), so
it can be bound onto the class via ``mock.patch.object`` for a full offline run.
"""

from __future__ import annotations

from typing import Any, Optional, Type, Union

from pydantic import BaseModel

from pipeline.evals.cassette import Cassette


class NoOpProvider:
    """Minimal ``LLMProvider`` for the orchestrator's ``llm=`` slot — structured
    output is served by the patched ``complete_structured``, not by this."""

    def __init__(self, model: str = "replay/none"):
        self.model = model
        # Some code paths read provider_config.model as a fallback.
        self.provider_config = type("PC", (), {"model": model, "api_key": "", "api_base": "", "provider": "replay"})()

    def complete(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        agent_name: str = "unknown",
        node_id: str = "",
    ) -> str:
        return ""

    def complete_json(
        self,
        prompt: str,
        system: str = "You are a precise JSON extraction assistant.",
        agent_name: str = "unknown",
        node_id: str = "",
        max_tokens: int = 8192,
    ) -> Union[list, dict]:
        return []


def make_structured_replay(cassette: Cassette):
    """
    Build a replacement for ``AgentRunner.complete_structured`` that serves the
    next recorded response from ``cassette`` for the call's ``(agent_name,
    node_id)``, validated into the requested ``response_model``. Raises
    ``CassetteMiss`` (loudly) on an unknown/exhausted key.
    """

    def complete_structured(
        runner_self,
        *,
        prompt: str,
        response_model: Type[BaseModel],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        agent_name: Optional[str] = None,
        node_id: Optional[str] = None,
    ) -> Any:
        recorded = cassette.next(agent_name, node_id)
        return response_model.model_validate(recorded)

    return complete_structured


__all__ = ["NoOpProvider", "make_structured_replay"]
