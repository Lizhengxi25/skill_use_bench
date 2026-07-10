"""MiniMax reasoning defaults for the SkillsBench rollout wrapper."""

from skillsbench_x.rollout import is_minimax_model, resolve_reasoning_effort


def test_minimax_provider_detection():
    assert is_minimax_model("minimax/MiniMax-M3")
    assert is_minimax_model("openrouter/minimax/minimax-m3")
    assert not is_minimax_model("openrouter/anthropic/claude-sonnet-4")


def test_codex_minimax_defaults_to_adaptive_thinking():
    assert resolve_reasoning_effort("codex-acp", "minimax/MiniMax-M3", None) == "high"
    assert (
        resolve_reasoning_effort(
            "codex-acp",
            "openrouter/minimax/minimax-m3",
            None,
        )
        == "high"
    )


def test_explicit_reasoning_wins_and_non_minimax_is_unchanged():
    assert resolve_reasoning_effort("codex-acp", "minimax/MiniMax-M3", "none") == "none"
    assert resolve_reasoning_effort("codex-acp", "gpt-5.5", None) is None
