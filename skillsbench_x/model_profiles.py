"""Project-owned rollout model capabilities.

BenchFlow owns generic provider and agent mechanics.  This registry owns the
experiment contract for models intentionally supported by this project:
context size, harness compatibility, and the logical reasoning-effort labels
that may appear in candidate identities.  ``default`` always means that the
caller omitted an effort override.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_REASONING_EFFORT = "default"
OMIT_REASONING_REQUEST_FILTER = "omit-reasoning"


@dataclass(frozen=True)
class HarnessModelProfile:
    agent: str
    reasoning_efforts: tuple[str, ...]
    default_request_filter: str | None = None
    explicit_reasoning_via_request_filter: bool = False


@dataclass(frozen=True)
class RolloutModelProfile:
    model: str
    context_window: int
    harnesses: dict[str, HarnessModelProfile]


def _openrouter_dual_harness_profile(
    model: str,
    *,
    context_window: int,
    reasoning_efforts: tuple[str, ...],
) -> RolloutModelProfile:
    return RolloutModelProfile(
        model=model,
        context_window=context_window,
        harnesses={
            "codex": HarnessModelProfile(
                agent="codex-acp",
                reasoning_efforts=reasoning_efforts,
                default_request_filter=OMIT_REASONING_REQUEST_FILTER,
            ),
            "claude-code": HarnessModelProfile(
                agent="claude-agent-acp",
                reasoning_efforts=reasoning_efforts,
                default_request_filter=OMIT_REASONING_REQUEST_FILTER,
                explicit_reasoning_via_request_filter=True,
            ),
        },
    )


ROLLOUT_MODEL_PROFILES: dict[str, RolloutModelProfile] = {
    "openrouter/qwen/qwen3-coder-next": _openrouter_dual_harness_profile(
        "openrouter/qwen/qwen3-coder-next",
        context_window=262_144,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT,),
    ),
    "openrouter/tencent/hy3": _openrouter_dual_harness_profile(
        "openrouter/tencent/hy3",
        context_window=262_144,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT, "none", "low", "high"),
    ),
    "openrouter/deepseek/deepseek-v4-flash": _openrouter_dual_harness_profile(
        "openrouter/deepseek/deepseek-v4-flash",
        context_window=1_048_576,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT, "high", "xhigh"),
    ),
    "openrouter/deepseek/deepseek-v4-pro": _openrouter_dual_harness_profile(
        "openrouter/deepseek/deepseek-v4-pro",
        context_window=1_048_576,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT, "high", "xhigh"),
    ),
    "openrouter/moonshotai/kimi-k2.6": _openrouter_dual_harness_profile(
        "openrouter/moonshotai/kimi-k2.6",
        context_window=262_144,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT,),
    ),
    "openrouter/z-ai/glm-5.2": _openrouter_dual_harness_profile(
        "openrouter/z-ai/glm-5.2",
        context_window=1_048_576,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT, "high", "xhigh"),
    ),
    "openrouter/qwen/qwen3.5-397b-a17b": _openrouter_dual_harness_profile(
        "openrouter/qwen/qwen3.5-397b-a17b",
        context_window=262_144,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT,),
    ),
    "openrouter/openai/gpt-oss-120b": _openrouter_dual_harness_profile(
        "openrouter/openai/gpt-oss-120b",
        context_window=131_072,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT, "low", "medium", "high"),
    ),
}


def get_rollout_model_profile(model: str) -> RolloutModelProfile | None:
    """Return an explicitly supported profile; unknown models keep legacy behavior."""
    return ROLLOUT_MODEL_PROFILES.get(model)


def reasoning_effort_label(reasoning: str | None) -> str:
    return reasoning if reasoning is not None else DEFAULT_REASONING_EFFORT


def _resolve_harness_profile(
    profile: RolloutModelProfile,
    *,
    harness: str | None,
    agent: str | None,
) -> tuple[str, HarnessModelProfile]:
    if harness is not None:
        harness_profile = profile.harnesses.get(harness)
        if harness_profile is None:
            raise ValueError(
                f"model {profile.model!r} does not support harness {harness!r}; supported harnesses: {', '.join(profile.harnesses)}"
            )
        if agent is not None and agent != harness_profile.agent:
            raise ValueError(
                f"model {profile.model!r} uses agent {harness_profile.agent!r} with harness {harness!r}; got agent override {agent!r}"
            )
        return harness, harness_profile

    if agent is None:
        raise ValueError("registered model validation requires a harness or agent")
    matches = [(name, harness_profile) for name, harness_profile in profile.harnesses.items() if harness_profile.agent == agent]
    if len(matches) != 1:
        supported = ", ".join(f"{name} ({item.agent})" for name, item in profile.harnesses.items())
        raise ValueError(f"model {profile.model!r} does not support agent {agent!r}; supported harnesses: {supported}")
    return matches[0]


def validate_rollout_model_candidate(
    model: str,
    *,
    reasoning: str | None,
    harness: str | None = None,
    agent: str | None = None,
) -> RolloutModelProfile | None:
    """Validate a registered model and return its profile.

    Models not yet in the registry deliberately retain the pre-registry
    behavior while they are migrated one at a time.
    """
    profile = get_rollout_model_profile(model)
    if profile is None:
        return None
    harness_name, harness_profile = _resolve_harness_profile(
        profile,
        harness=harness,
        agent=agent,
    )
    effort = reasoning_effort_label(reasoning)
    if effort not in harness_profile.reasoning_efforts:
        supported = ", ".join(harness_profile.reasoning_efforts)
        raise ValueError(f"model {model!r} with harness {harness_name!r} supports reasoning efforts: {supported}; got {effort!r}")
    return profile


def model_runtime_agent_env(
    model: str,
    *,
    agent: str,
    reasoning: str | None,
) -> dict[str, str]:
    """Translate a registered model profile into generic BenchFlow env knobs."""
    profile = validate_rollout_model_candidate(
        model,
        agent=agent,
        reasoning=reasoning,
    )
    if profile is None:
        return {}
    _, harness_profile = _resolve_harness_profile(
        profile,
        harness=None,
        agent=agent,
    )
    env = {"BENCHFLOW_PROVIDER_MODEL_CONTEXT_WINDOW": str(profile.context_window)}
    if reasoning is None and harness_profile.default_request_filter:
        env["BENCHFLOW_PROVIDER_REQUEST_FILTER"] = harness_profile.default_request_filter
    elif reasoning is not None and harness_profile.explicit_reasoning_via_request_filter:
        env["BENCHFLOW_PROVIDER_REQUEST_FILTER"] = f"reasoning-effort:{reasoning}"
    return env


def model_runtime_reasoning_effort(
    model: str,
    *,
    agent: str,
    reasoning: str | None,
) -> str | None:
    """Return the effort passed to the harness CLI after profile translation."""
    profile = validate_rollout_model_candidate(
        model,
        agent=agent,
        reasoning=reasoning,
    )
    if profile is None or reasoning is None:
        return reasoning
    _, harness_profile = _resolve_harness_profile(
        profile,
        harness=None,
        agent=agent,
    )
    if harness_profile.explicit_reasoning_via_request_filter:
        return None
    return reasoning
