"""Project-owned rollout model capabilities.

BenchFlow owns generic provider and agent mechanics. This registry owns the
experiment contract for models intentionally supported by this project:
context size, harness compatibility, and the logical reasoning-effort labels
that may appear in candidate identities. ``default`` always means that the
caller omitted an effort override.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

DEFAULT_REASONING_EFFORT = "default"
OMIT_REASONING_REQUEST_FILTER = "omit-reasoning"


@dataclass(frozen=True)
class HarnessModelProfile:
    agent: str
    reasoning_efforts: tuple[str, ...]
    pass_reasoning_to_benchflow: bool = False
    reasoning_env: str | None = None


@dataclass(frozen=True)
class RolloutModelProfile:
    model: str
    display_name: str
    context_window: int
    max_output_tokens: int
    codex_default_reasoning_level: str
    codex_reasoning_levels: tuple[str, ...]
    input_modalities: tuple[str, ...]
    harnesses: dict[str, HarnessModelProfile]
    supports_reasoning_summaries: bool = True
    claude_max_output_tokens: int | None = None

    def codex_model_profile_json(self) -> str:
        levels = [
            {
                "effort": effort,
                "description": f"{effort.capitalize()} reasoning",
            }
            for effort in self.codex_reasoning_levels
        ]
        payload = {
            "display_name": f"{self.display_name} (OpenRouter)",
            "description": f"{self.display_name} via OpenRouter",
            "default_reasoning_level": self.codex_default_reasoning_level,
            "supported_reasoning_levels": levels,
            "base_instructions": (
                f"You are Codex, a coding agent based on {self.display_name}. "
                "You and the user share the same workspace. Complete the assigned "
                "task by inspecting the repository, using the tools exposed by the "
                "harness, editing files as needed, and verifying your work."
            ),
            "supports_reasoning_summaries": self.supports_reasoning_summaries,
            "input_modalities": list(self.input_modalities),
        }
        return json.dumps(payload, separators=(",", ":"))


def _openrouter_harnesses(
    reasoning_efforts: tuple[str, ...],
    *,
    openhands: bool = False,
) -> dict[str, HarnessModelProfile]:
    harnesses = {
        "codex": HarnessModelProfile(
            agent="codex-acp",
            reasoning_efforts=reasoning_efforts,
            pass_reasoning_to_benchflow=True,
        ),
        "claude-code": HarnessModelProfile(
            agent="claude-agent-acp",
            reasoning_efforts=reasoning_efforts,
        ),
    }
    if openhands:
        harnesses["openhands"] = HarnessModelProfile(
            agent="openhands",
            reasoning_efforts=reasoning_efforts,
            reasoning_env="LLM_REASONING_EFFORT",
        )
    return harnesses


def _openrouter_profile(
    model: str,
    *,
    display_name: str,
    context_window: int,
    max_output_tokens: int,
    reasoning_efforts: tuple[str, ...],
    codex_default_reasoning_level: str,
    codex_reasoning_levels: tuple[str, ...],
    input_modalities: tuple[str, ...] = ("text",),
    openhands: bool = False,
    supports_reasoning_summaries: bool = True,
    claude_max_output_tokens: int | None = None,
) -> RolloutModelProfile:
    return RolloutModelProfile(
        model=model,
        display_name=display_name,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        codex_default_reasoning_level=codex_default_reasoning_level,
        codex_reasoning_levels=codex_reasoning_levels,
        input_modalities=input_modalities,
        harnesses=_openrouter_harnesses(
            reasoning_efforts,
            openhands=openhands,
        ),
        supports_reasoning_summaries=supports_reasoning_summaries,
        claude_max_output_tokens=claude_max_output_tokens,
    )


ROLLOUT_MODEL_PROFILES: dict[str, RolloutModelProfile] = {
    "openrouter/qwen/qwen3-coder-next": _openrouter_profile(
        "openrouter/qwen/qwen3-coder-next",
        display_name="Qwen3 Coder Next",
        context_window=262_144,
        max_output_tokens=65_536,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT,),
        codex_default_reasoning_level="none",
        codex_reasoning_levels=("none",),
        supports_reasoning_summaries=False,
    ),
    "openrouter/tencent/hy3": _openrouter_profile(
        "openrouter/tencent/hy3",
        display_name="Tencent HY3",
        context_window=262_144,
        max_output_tokens=65_536,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT, "none", "low", "high"),
        codex_default_reasoning_level="none",
        codex_reasoning_levels=("none", "low", "high"),
        openhands=True,
    ),
    "openrouter/deepseek/deepseek-v4-flash": _openrouter_profile(
        "openrouter/deepseek/deepseek-v4-flash",
        display_name="DeepSeek V4 Flash",
        context_window=1_048_576,
        max_output_tokens=131_072,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT, "high", "xhigh"),
        codex_default_reasoning_level="high",
        codex_reasoning_levels=("high", "xhigh"),
        openhands=True,
    ),
    "openrouter/deepseek/deepseek-v4-pro": _openrouter_profile(
        "openrouter/deepseek/deepseek-v4-pro",
        display_name="DeepSeek V4 Pro",
        context_window=1_048_576,
        max_output_tokens=131_072,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT, "high", "xhigh"),
        codex_default_reasoning_level="high",
        codex_reasoning_levels=("high", "xhigh"),
    ),
    "openrouter/moonshotai/kimi-k2.6": _openrouter_profile(
        "openrouter/moonshotai/kimi-k2.6",
        display_name="Kimi K2.6",
        context_window=262_144,
        max_output_tokens=65_536,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT,),
        codex_default_reasoning_level="high",
        codex_reasoning_levels=("high",),
        input_modalities=("text", "image"),
    ),
    "openrouter/z-ai/glm-5.2": _openrouter_profile(
        "openrouter/z-ai/glm-5.2",
        display_name="GLM 5.2",
        context_window=1_048_576,
        max_output_tokens=131_072,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT, "high", "xhigh"),
        codex_default_reasoning_level="high",
        codex_reasoning_levels=("high", "xhigh"),
        openhands=True,
    ),
    "openrouter/qwen/qwen3.5-397b-a17b": _openrouter_profile(
        "openrouter/qwen/qwen3.5-397b-a17b",
        display_name="Qwen3.5 397B A17B",
        context_window=262_144,
        max_output_tokens=65_536,
        reasoning_efforts=(DEFAULT_REASONING_EFFORT,),
        codex_default_reasoning_level="high",
        codex_reasoning_levels=("high",),
        input_modalities=("text", "image"),
        openhands=True,
    ),
    "openrouter/openai/gpt-oss-120b": _openrouter_profile(
        "openrouter/openai/gpt-oss-120b",
        display_name="GPT-OSS-120B",
        context_window=131_072,
        max_output_tokens=65_536,
        reasoning_efforts=(
            DEFAULT_REASONING_EFFORT,
            "low",
            "medium",
            "high",
        ),
        codex_default_reasoning_level="medium",
        codex_reasoning_levels=("low", "medium", "high"),
        openhands=True,
        claude_max_output_tokens=65_536,
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
    """Validate a registered model and return its profile."""
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
    request_filter = OMIT_REASONING_REQUEST_FILTER if reasoning is None else f"reasoning-effort:{reasoning}"
    env = {
        "BENCHFLOW_PROVIDER_MODEL_CONTEXT_WINDOW": str(profile.context_window),
        "BENCHFLOW_PROVIDER_REQUEST_FILTER": request_filter,
    }
    if agent == "codex-acp":
        env.update(
            {
                "BENCHFLOW_CODEX_MODEL_PROFILE_JSON": (profile.codex_model_profile_json()),
                "BENCHFLOW_CODEX_SANDBOX_MODE": "danger-full-access",
            }
        )
    elif agent == "claude-agent-acp":
        env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(profile.context_window)
        if profile.claude_max_output_tokens is not None:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(profile.claude_max_output_tokens)
    elif agent == "openhands":
        env.update(
            {
                "LLM_MAX_INPUT_TOKENS": str(profile.context_window),
                "LLM_MAX_OUTPUT_TOKENS": str(profile.max_output_tokens),
                "LLM_TIMEOUT": "115200",
            }
        )
    if reasoning is not None and harness_profile.reasoning_env:
        env[harness_profile.reasoning_env] = reasoning
    return env


def model_runtime_reasoning_effort(
    model: str,
    *,
    agent: str,
    reasoning: str | None,
) -> str | None:
    """Return the effort passed to BenchFlow after profile translation."""
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
    return reasoning if harness_profile.pass_reasoning_to_benchflow else None
