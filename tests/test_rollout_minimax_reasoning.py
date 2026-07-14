"""Reasoning forwarding for the SkillsBench rollout wrapper."""

from skillsbench_x.rollout import resolve_reasoning_effort


def test_omitted_reasoning_stays_none_for_minimax():
    assert resolve_reasoning_effort("codex-acp", "minimax/MiniMax-M3", None) is None
    assert resolve_reasoning_effort(
        "codex-acp",
        "openrouter/minimax/minimax-m3",
        None,
    ) is None


def test_explicit_reasoning_is_forwarded_unchanged():
    assert resolve_reasoning_effort("codex-acp", "minimax/MiniMax-M3", "none") == "none"
    assert resolve_reasoning_effort("codex-acp", "minimax/MiniMax-M3", "high") == "high"


def test_non_minimax_omitted_reasoning_stays_none():
    assert resolve_reasoning_effort("codex-acp", "gpt-5.5", None) is None
