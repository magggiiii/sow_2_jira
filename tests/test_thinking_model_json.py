"""Both LLMClient.complete_json and pageindex.utils.extract_json must
survive thinking-model output that prefixes its JSON with a
``<think>...</think>`` reasoning block (Qwen3-thinking, DeepSeek-R1,
Gemini "thinking mode", o1-style). Two real shapes seen in the wild:

  1. Full block: ``<think>I should ...</think>\\n[{"a": 1}]``
  2. Stray closer: ``</think>\\n\\n[{"a": 1}]`` (model truncated the opener)

Both must round-trip cleanly.
"""

from unittest.mock import MagicMock

from pageindex.utils import extract_json


def test_pageindex_extract_json_strips_full_think_block():
    raw = (
        '<think>The TOC is on page 4 based on...</think>\n'
        '[{"structure": "1", "title": "Executive Summary"}]'
    )
    assert extract_json(raw) == [{"structure": "1", "title": "Executive Summary"}]


def test_pageindex_extract_json_strips_stray_closing_think():
    raw = '</think>\n\n[\n  {"structure": "1", "title": "Executive Summary"}\n]'
    assert extract_json(raw) == [{"structure": "1", "title": "Executive Summary"}]


def test_pageindex_extract_json_handles_object_with_think_prefix():
    raw = '<think>reasoning here</think>\n{"toc_found": true, "page": 4}'
    assert extract_json(raw) == {"toc_found": True, "page": 4}


def test_pageindex_extract_json_emergency_handles_array():
    # Malformed but partially valid — emergency cleanup must try array bounds too.
    raw = 'garbage [\n  {"a": 1}\n] trailing garbage'
    assert extract_json(raw) == [{"a": 1}]


def test_pageindex_extract_json_no_thinking_unchanged():
    # Don't break clean JSON.
    raw = '[{"a": 1}, {"b": 2}]'
    assert extract_json(raw) == [{"a": 1}, {"b": 2}]


def test_complete_json_strips_think_block():
    """LLMClient.complete_json runs the same scrub on remote agent calls."""
    from pipeline.llm_client import LLMClient

    audit = MagicMock()
    audit.log = MagicMock()

    # Build a client without invoking the real LLM. The complete() method is
    # what we mock; complete_json just post-processes its return value.
    client = LLMClient.__new__(LLMClient)
    client.audit_logger = audit
    client.run_id = "test"
    client.complete = MagicMock(
        return_value='<think>I should return JSON.</think>\n[{"title": "Implement auth"}]'
    )

    result = client.complete_json(prompt="x", agent_name="test")
    assert result == [{"title": "Implement auth"}]


def test_complete_json_strips_stray_closing_think():
    from pipeline.llm_client import LLMClient
    audit = MagicMock()

    client = LLMClient.__new__(LLMClient)
    client.audit_logger = audit
    client.run_id = "test"
    client.complete = MagicMock(
        return_value='</think>\n\n{"foo": "bar"}'
    )

    assert client.complete_json(prompt="x", agent_name="test") == {"foo": "bar"}
