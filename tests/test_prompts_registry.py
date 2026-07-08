# tests/test_prompts_registry.py
"""
STEP 3.6a: prompts registry — a BYTE-FAITHFUL move of the six agents' inline
system/user prompt constants into ``prompts/*.v1.txt`` loaded via
``prompts/registry.py``.

The guard is a frozen sha256 snapshot of each prompt, computed from the ORIGINAL
inline constants before the move. Two independent assertions per prompt:

  * ``registry.load(key)`` -> bytes hash to the frozen value (the .txt file is
    byte-exact and can never silently drift).
  * the agent module's constant -> hashes to the same frozen value AND is the
    value the registry serves (proves the agent now sources the prompt from the
    registry, with zero change to the bytes it sends to the model).

Existing per-agent behavior tests (test_extraction.py, test_critic.py, …) prove
the rendered prompts and mutations are unchanged; this file pins the raw text.
"""

from __future__ import annotations

import hashlib

import pytest

from prompts import registry

# Frozen sha256 of each prompt, captured from the inline constants at move time.
FROZEN = {
    "extraction.system.v1": "54a105b249f3054cd5b923b43fa9bd3f5da0e5ed4a59451a9177a81b8dbc9bb7",
    "extraction.user.v1": "55bb1bdc07c51ca04fcb95f8e5156981cdd0499fce8db7bde324a91aa7ca4a2c",
    "classifier.system.v1": "50ef7d8215a8c30d91d4904682c17c87498dfff4b84e5b24ed36aa1c8b6e2ddf",
    "classifier.user.v1": "bb9288fa83e07992de1f4d55d5c83f608c46808eb0c5ae8f07dbc9c8fde0b1e1",
    "dedup.system.v1": "4e3e9dabaa44f01087926e5f2538a924243b86df70598e2e86c100fd348b4638",
    "dedup.user.v1": "ec926e3ad00695c748572bf734599409fd58cc377ed05194501e75a00a26a644",
    "critic.system.v1": "86acc6491beaedfa63abc5e2e4ed2d24b6839c850fc92942c955678d3a743375",
    "critic.user.v1": "f9a0d56935b08840c8edefe6e194f4239d1dfc9af489a52281ebfe87be06fb09",
    "coverage.system.v1": "72c0c1999383df41d7ffe7e76f08b74f47db3281afacfbff876b3e647ade35f3",
    "coverage.user.v1": "f1d0d79a68dbcfd3bc7af0d530a895bd78ca1e9bcbfd3b3d8959fd8cd87b2fce",
    "gap.system.v1": "97a59c7992a39c8a4558669f347cb7667c7d9d293fe16a4a489cdd8fdf289bfb",
    "gap.user.v1": "5c3a1701ab67c703d7ff9295400090f00708876bd6ba2d83b2fafae6b48aa27d",
}


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _agent_constant(key: str) -> str:
    """The current module-level constant for ``key`` (post-move this is the
    registry-sourced value; the test still asserts its bytes independently)."""
    from pipeline.agents import (
        classifier,
        coverage_check,
        critic,
        deduplication,
        extraction,
        gap_recovery,
    )

    mapping = {
        "extraction.system.v1": extraction.EXTRACTION_SYSTEM_PROMPT,
        "extraction.user.v1": extraction.EXTRACTION_PROMPT_TEMPLATE,
        "classifier.system.v1": classifier.CLASSIFIER_SYSTEM_PROMPT,
        "classifier.user.v1": classifier.CLASSIFIER_PROMPT_TEMPLATE,
        "dedup.system.v1": deduplication.DEDUP_SYSTEM_PROMPT,
        "dedup.user.v1": deduplication.DEDUP_PROMPT_TEMPLATE,
        "critic.system.v1": critic.CRITIC_SYSTEM_PROMPT,
        "critic.user.v1": critic.CRITIC_PROMPT_TEMPLATE,
        "coverage.system.v1": coverage_check.COVERAGE_SYSTEM_PROMPT,
        "coverage.user.v1": coverage_check.COVERAGE_PROMPT_TEMPLATE,
        "gap.system.v1": gap_recovery.GAP_SYSTEM_PROMPT,
        "gap.user.v1": gap_recovery.GAP_PROMPT_TEMPLATE,
    }
    return mapping[key]


# ─── registry serves the byte-exact prompt ─────────────────────────────────────


@pytest.mark.parametrize("key", sorted(FROZEN))
def test_registry_prompt_matches_frozen_snapshot(key):
    loaded = registry.load(key)
    assert isinstance(loaded, str)
    assert _sha(loaded) == FROZEN[key], f"{key} drifted from its frozen snapshot"


# ─── agent constant is byte-identical AND registry-sourced ─────────────────────


@pytest.mark.parametrize("key", sorted(FROZEN))
def test_agent_constant_matches_frozen_snapshot(key):
    assert _sha(_agent_constant(key)) == FROZEN[key]


@pytest.mark.parametrize("key", sorted(FROZEN))
def test_agent_constant_is_registry_sourced(key):
    # The literal lives only in the .txt now; the agent constant IS the cached
    # object the registry serves (identity, not just equality) — proving the move
    # removed the inline copy rather than duplicating it.
    assert _agent_constant(key) is registry.load(key)


# ─── registry semantics ────────────────────────────────────────────────────────


def test_load_is_cached_returns_same_object():
    assert registry.load("extraction.system.v1") is registry.load("extraction.system.v1")


def test_unknown_prompt_raises():
    with pytest.raises((KeyError, FileNotFoundError)):
        registry.load("does.not.exist.v1")


def test_format_placeholders_preserved_for_a_template():
    # The user templates are str.format templates; the move must keep the
    # {placeholders} intact so .format still renders identically.
    tmpl = registry.load("gap.user.v1")
    assert "{section_text}" in tmpl
