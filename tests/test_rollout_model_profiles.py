"""Project model-profile validation and BenchFlow runtime translation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from skillsbench_x import rollout
from skillsbench_x.model_profiles import (
    DEFAULT_REASONING_EFFORT,
    get_rollout_model_profile,
    model_runtime_agent_env,
    model_runtime_reasoning_effort,
    validate_rollout_model_candidate,
)
from skillsbench_x.rollout import resolve_reasoning_effort

QWEN = "openrouter/qwen/qwen3-coder-next"
HY3 = "openrouter/tencent/hy3"
DEEPSEEK_FLASH = "openrouter/deepseek/deepseek-v4-flash"
DEEPSEEK_PRO = "openrouter/deepseek/deepseek-v4-pro"
KIMI_K26 = "openrouter/moonshotai/kimi-k2.6"
MINIMAX_M3 = "openrouter/minimax/minimax-m3"
MINIMAX_M27 = "openrouter/minimax/minimax-m2.7"
GLM_51 = "openrouter/z-ai/glm-5.1"
GLM_52 = "openrouter/z-ai/glm-5.2"
QWEN_35_397B = "openrouter/qwen/qwen3.5-397b-a17b"
GPT_OSS_120B = "openrouter/openai/gpt-oss-120b"
REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_MODEL_LIMITS = (
    (HY3, 262_144, 65_536),
    (GPT_OSS_120B, 131_072, 65_536),
    (QWEN_35_397B, 262_144, 65_536),
    (GLM_51, 200_000, 128_000),
    (GLM_52, 1_048_576, 131_072),
    (KIMI_K26, 262_144, 65_536),
    (MINIMAX_M3, 524_288, 131_072),
    (MINIMAX_M27, 196_608, 131_072),
    (DEEPSEEK_PRO, 1_048_576, 131_072),
    (DEEPSEEK_FLASH, 1_048_576, 131_072),
)


def test_rollout_script_supports_documented_direct_entrypoint():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "skillsbench_x" / "rollout.py"), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "BenchFlow-driven rollout" in result.stdout


def test_qwen_profile_supports_both_harnesses_at_default_only():
    profile = get_rollout_model_profile(QWEN)

    assert profile is not None
    assert profile.context_window == 262_144
    assert set(profile.harnesses) == {"codex", "claude-code"}
    assert all(item.reasoning_efforts == (DEFAULT_REASONING_EFFORT,) for item in profile.harnesses.values())


@pytest.mark.parametrize(
    ("harness", "agent"),
    [("codex", "codex-acp"), ("claude-code", "claude-agent-acp")],
)
def test_qwen_default_is_valid_and_enables_runtime_filter(harness, agent):
    profile = validate_rollout_model_candidate(
        QWEN,
        harness=harness,
        agent=agent,
        reasoning=None,
    )

    assert profile is not None
    assert resolve_reasoning_effort(agent, QWEN, None) is None
    env = model_runtime_agent_env(QWEN, agent=agent, reasoning=None)
    assert env["BENCHFLOW_PROVIDER_MODEL_CONTEXT_WINDOW"] == "262144"
    assert env["BENCHFLOW_PROVIDER_REQUEST_FILTER"] == "omit-reasoning"


@pytest.mark.parametrize("reasoning", ["none", "minimal", "low", "medium", "high", "xhigh"])
def test_qwen_rejects_every_explicit_reasoning_effort(reasoning):
    with pytest.raises(ValueError, match="supports reasoning efforts: default"):
        validate_rollout_model_candidate(
            QWEN,
            harness="codex",
            reasoning=reasoning,
        )


def test_qwen_rejects_unregistered_agent_override():
    with pytest.raises(ValueError, match="agent override 'other-acp'"):
        validate_rollout_model_candidate(
            QWEN,
            harness="codex",
            agent="other-acp",
            reasoning=None,
        )


def test_rollout_entrypoint_rejects_agent_that_does_not_match_harness():
    with pytest.raises(
        ValueError,
        match=r"uses agent 'openhands'.*got agent override",
    ):
        resolve_reasoning_effort(
            "codex-acp",
            GLM_52,
            None,
            harness="openhands",
        )


@pytest.mark.parametrize(
    ("model", "context_window", "reasoning_efforts"),
    [
        (HY3, 262_144, ("default", "none", "low", "high")),
        (DEEPSEEK_FLASH, 1_048_576, ("default", "high", "xhigh")),
        (DEEPSEEK_PRO, 1_048_576, ("default", "high", "xhigh")),
        (KIMI_K26, 262_144, ("default",)),
        (MINIMAX_M3, 524_288, ("default", "none", "low", "medium", "high")),
        (MINIMAX_M27, 196_608, ("default", "none", "low", "medium", "high")),
        (GLM_51, 200_000, ("default",)),
        (GLM_52, 1_048_576, ("default", "high", "xhigh")),
        (QWEN_35_397B, 262_144, ("default",)),
        (GPT_OSS_120B, 131_072, ("default", "low", "medium", "high")),
    ],
)
def test_new_openrouter_profiles_expose_expected_harnesses(
    model,
    context_window,
    reasoning_efforts,
):
    profile = get_rollout_model_profile(model)

    assert profile is not None
    assert profile.context_window == context_window
    expected = {"codex", "claude-code"}
    if model in {
        HY3,
        DEEPSEEK_FLASH,
        DEEPSEEK_PRO,
        KIMI_K26,
        MINIMAX_M3,
        MINIMAX_M27,
        GLM_51,
        GLM_52,
        QWEN_35_397B,
        GPT_OSS_120B,
    }:
        expected.add("openhands")
    assert set(profile.harnesses) == expected
    assert all(item.reasoning_efforts == reasoning_efforts for item in profile.harnesses.values())


@pytest.mark.parametrize(
    "model",
    [
        HY3,
        DEEPSEEK_FLASH,
        DEEPSEEK_PRO,
        KIMI_K26,
        MINIMAX_M3,
        MINIMAX_M27,
        GLM_51,
        GLM_52,
        QWEN_35_397B,
        GPT_OSS_120B,
    ],
)
def test_new_openrouter_profiles_preserve_true_default(model):
    profile = get_rollout_model_profile(model)
    assert profile is not None

    for agent in ("codex-acp", "claude-agent-acp"):
        env = model_runtime_agent_env(model, agent=agent, reasoning=None)
        assert env["BENCHFLOW_PROVIDER_MODEL_CONTEXT_WINDOW"] == str(profile.context_window)
        assert env["BENCHFLOW_PROVIDER_REQUEST_FILTER"] == "omit-reasoning"
        assert model_runtime_reasoning_effort(model, agent=agent, reasoning=None) is None


@pytest.mark.parametrize(
    ("model", "accepted", "rejected"),
    [
        (HY3, ("none", "low", "high"), ("minimal", "medium", "xhigh")),
        (DEEPSEEK_FLASH, ("high", "xhigh"), ("none", "minimal", "low", "medium")),
        (DEEPSEEK_PRO, ("high", "xhigh"), ("none", "minimal", "low", "medium")),
        (KIMI_K26, (), ("none", "minimal", "low", "medium", "high", "xhigh")),
        (MINIMAX_M3, ("none", "low", "medium", "high"), ("minimal", "xhigh")),
        (MINIMAX_M27, ("none", "low", "medium", "high"), ("minimal", "xhigh")),
        (GLM_51, (), ("none", "minimal", "low", "medium", "high", "xhigh")),
        (GLM_52, ("high", "xhigh"), ("none", "minimal", "low", "medium")),
        (QWEN_35_397B, (), ("none", "minimal", "low", "medium", "high", "xhigh")),
        (GPT_OSS_120B, ("low", "medium", "high"), ("none", "minimal", "xhigh")),
    ],
)
def test_new_openrouter_profiles_enforce_reasoning_ranges(model, accepted, rejected):
    for reasoning in accepted:
        assert validate_rollout_model_candidate(model, harness="codex", reasoning=reasoning) is not None
    for reasoning in rejected:
        with pytest.raises(ValueError, match="supports reasoning efforts"):
            validate_rollout_model_candidate(model, harness="codex", reasoning=reasoning)


@pytest.mark.parametrize(
    ("model", "reasoning"),
    [
        (HY3, "none"),
        (HY3, "low"),
        (HY3, "high"),
        (DEEPSEEK_FLASH, "xhigh"),
        (DEEPSEEK_PRO, "high"),
        (GLM_52, "xhigh"),
        (GPT_OSS_120B, "medium"),
    ],
)
def test_claude_explicit_effort_is_applied_at_openrouter_request_boundary(model, reasoning):
    profile = get_rollout_model_profile(model)
    assert profile is not None

    env = model_runtime_agent_env(
        model,
        agent="claude-agent-acp",
        reasoning=reasoning,
    )
    assert env["BENCHFLOW_PROVIDER_MODEL_CONTEXT_WINDOW"] == str(profile.context_window)
    assert env["BENCHFLOW_PROVIDER_REQUEST_FILTER"] == (f"reasoning-effort:{reasoning}")
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == str(profile.context_window)
    if model == GPT_OSS_120B:
        assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "65536"
    else:
        assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in env
    assert model_runtime_reasoning_effort(model, agent="claude-agent-acp", reasoning=reasoning) is None
    assert model_runtime_reasoning_effort(model, agent="codex-acp", reasoning=reasoning) == reasoning


@pytest.mark.parametrize(
    ("model", "reasoning", "expected_output_tokens"),
    [
        (HY3, None, 65_536),
        (DEEPSEEK_FLASH, None, 131_072),
        (DEEPSEEK_PRO, None, 131_072),
        (KIMI_K26, None, 65_536),
        (MINIMAX_M3, None, 131_072),
        (MINIMAX_M27, None, 131_072),
        (GLM_51, None, 128_000),
        (GLM_52, None, 131_072),
        (GLM_52, "xhigh", 131_072),
        (QWEN_35_397B, None, 65_536),
        (GPT_OSS_120B, "low", 65_536),
    ],
)
def test_openhands_runtime_uses_profile_limits_and_provider_filter(
    model,
    reasoning,
    expected_output_tokens,
):
    profile = get_rollout_model_profile(model)
    assert profile is not None

    env = model_runtime_agent_env(
        model,
        agent="openhands",
        reasoning=reasoning,
    )

    assert env["LLM_MAX_INPUT_TOKENS"] == str(profile.context_window)
    assert env["LLM_MAX_OUTPUT_TOKENS"] == str(expected_output_tokens)
    assert env["LLM_TIMEOUT"] == "115200"
    assert env["BENCHFLOW_PROVIDER_REQUEST_FILTER"] == ("omit-reasoning" if reasoning is None else f"reasoning-effort:{reasoning}")
    if reasoning is None:
        assert "LLM_REASONING_EFFORT" not in env
    else:
        assert env["LLM_REASONING_EFFORT"] == reasoning
    assert (
        model_runtime_reasoning_effort(
            model,
            agent="openhands",
            reasoning=reasoning,
        )
        is None
    )


@pytest.mark.parametrize(
    ("harness", "agent"),
    [
        ("claude-code", "claude-agent-acp"),
        ("openhands", "openhands"),
    ],
)
@pytest.mark.parametrize(
    ("model", "context_window", "max_output_tokens"),
    TARGET_MODEL_LIMITS,
)
def test_target_default_command_omits_all_reasoning_effort_controls(
    harness,
    agent,
    model,
    context_window,
    max_output_tokens,
):
    command = rollout.build_bench_run_command(
        task_dir=REPO_ROOT / "tasks" / "jax-computing-basics",
        jobs_dir=REPO_ROOT / ".tmp-jobs",
        agent=agent,
        model=model,
        with_skills=False,
        bench_extra_args=[],
        reasoning_effort=None,
    )

    agent_env = {}
    for index, value in enumerate(command):
        if value != "--agent-env":
            continue
        key, env_value = command[index + 1].split("=", 1)
        agent_env[key] = env_value

    assert "--reasoning-effort" not in command
    assert agent_env["BENCHFLOW_PROVIDER_REQUEST_FILTER"] == "omit-reasoning"
    assert agent_env["BENCHFLOW_PROVIDER_MODEL_CONTEXT_WINDOW"] == str(context_window)
    assert "BENCHFLOW_REASONING_EFFORT" not in agent_env
    assert "LLM_REASONING_EFFORT" not in agent_env
    assert all(not value.startswith("reasoning-effort:") for value in agent_env.values())

    if harness == "openhands":
        assert agent_env["LLM_MAX_INPUT_TOKENS"] == str(context_window)
        assert agent_env["LLM_MAX_OUTPUT_TOKENS"] == str(max_output_tokens)
        assert agent_env["LLM_TIMEOUT"] == "115200"
    else:
        assert agent_env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == str(context_window)
        if model == GPT_OSS_120B:
            assert agent_env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "65536"
        else:
            assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in agent_env
        assert "LLM_MAX_INPUT_TOKENS" not in agent_env
        assert "LLM_MAX_OUTPUT_TOKENS" not in agent_env


@pytest.mark.parametrize(
    ("harness", "agent"),
    [
        ("claude-code", "claude-agent-acp"),
        ("openhands", "openhands"),
    ],
)
@pytest.mark.parametrize(
    ("model", "context_window", "max_output_tokens"),
    TARGET_MODEL_LIMITS,
)
def test_target_default_cli_dry_run_omits_all_reasoning_effort_controls(
    tmp_path,
    monkeypatch,
    capsys,
    harness,
    agent,
    model,
    context_window,
    max_output_tokens,
):
    runs_root = tmp_path / "runs"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rollout.py",
            "--tasks",
            str(REPO_ROOT / "tasks" / "jax-computing-basics"),
            "--harness",
            harness,
            "--model",
            model,
            "--dry-run",
            "--run-id",
            "default-effort-check",
            "--runs-root",
            str(runs_root),
        ],
    )

    assert rollout.main() == 0
    output = capsys.readouterr().out

    assert f"--agent {agent}" in output
    assert "--reasoning-effort" not in output
    assert "BENCHFLOW_REASONING_EFFORT" not in output
    assert "LLM_REASONING_EFFORT" not in output
    assert "reasoning-effort:" not in output
    assert "BENCHFLOW_PROVIDER_REQUEST_FILTER=omit-reasoning" in output
    assert f"BENCHFLOW_PROVIDER_MODEL_CONTEXT_WINDOW={context_window}" in output
    if harness == "openhands":
        assert f"LLM_MAX_INPUT_TOKENS={context_window}" in output
        assert f"LLM_MAX_OUTPUT_TOKENS={max_output_tokens}" in output
    else:
        assert f"CLAUDE_CODE_MAX_CONTEXT_TOKENS={context_window}" in output
        if model == GPT_OSS_120B:
            assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS=65536" in output
        else:
            assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in output
    assert not runs_root.exists()


def test_unregistered_models_keep_legacy_behavior():
    assert (
        validate_rollout_model_candidate(
            "gpt-5.5",
            harness="codex",
            reasoning="high",
        )
        is None
    )
    assert (
        model_runtime_agent_env(
            "gpt-5.5",
            agent="codex-acp",
            reasoning="high",
        )
        == {}
    )


@pytest.mark.parametrize(
    ("harness", "agent"),
    [
        ("claude-code", None),
        (None, "claude-agent-acp"),
    ],
)
def test_unregistered_claude_models_reject_explicit_reasoning_before_acp(harness, agent):
    """Regress Zed PR #260: CC 2.1.19 has model config but no ACP effort config."""
    with pytest.raises(
        ValueError,
        match=r"unregistered model .* cannot use explicit reasoning with Claude Code",
    ):
        validate_rollout_model_candidate(
            "openrouter/vendor/unregistered-model",
            harness=harness,
            agent=agent,
            reasoning="high",
        )


def test_qwen_runtime_profile_is_forwarded_to_benchflow(tmp_path, monkeypatch):
    task_dir = tmp_path / "task-one"
    task_dir.mkdir()
    captured: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        captured.append(cmd)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)
    monkeypatch.setattr(rollout, "locate_rollout_dir", lambda *_args: None)

    task_id, _, _ = rollout.run_one(
        task_dir=task_dir,
        run_dir=tmp_path / "runs",
        agent="claude-agent-acp",
        model=QWEN,
        with_skills=False,
        repo_root=Path(tmp_path),
        bench_extra_args=[],
        reasoning_effort=None,
    )

    command = captured[0]
    assert [
        "--agent-env",
        "BENCHFLOW_PROVIDER_MODEL_CONTEXT_WINDOW=262144",
    ] == command[command.index("--agent-env") : command.index("--agent-env") + 2]
    assert "BENCHFLOW_PROVIDER_REQUEST_FILTER=omit-reasoning" in command
    assert command[:5] == ["uv", "run", "bench", "eval", "run"]
    assert "--tasks-dir" in command
    assert command[command.index("--skill-mode") + 1] == "no-skill"
    assert command[command.index("--sandbox-user") + 1] == "agent"
    metadata = (tmp_path / "runs" / task_id / "metadata.tsv").read_text()
    assert "reasoning_effort_label\tdefault\n" in metadata
    assert "model_context_window\t262144\n" in metadata


def test_claude_explicit_effort_uses_request_filter_not_native_cli(tmp_path, monkeypatch):
    task_dir = tmp_path / "task-one"
    task_dir.mkdir()
    captured: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        captured.append(cmd)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)
    monkeypatch.setattr(rollout, "locate_rollout_dir", lambda *_args: None)

    rollout.run_one(
        task_dir=task_dir,
        run_dir=tmp_path / "runs",
        agent="claude-agent-acp",
        model=DEEPSEEK_PRO,
        with_skills=False,
        repo_root=Path(tmp_path),
        bench_extra_args=[],
        reasoning_effort="xhigh",
    )

    command = captured[0]
    assert "BENCHFLOW_PROVIDER_REQUEST_FILTER=reasoning-effort:xhigh" in command
    assert "--reasoning-effort" not in command


def test_codex_explicit_effort_keeps_native_responses_cli(tmp_path, monkeypatch):
    task_dir = tmp_path / "task-one"
    task_dir.mkdir()
    captured: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        captured.append(cmd)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)
    monkeypatch.setattr(rollout, "locate_rollout_dir", lambda *_args: None)

    rollout.run_one(
        task_dir=task_dir,
        run_dir=tmp_path / "runs",
        agent="codex-acp",
        model=DEEPSEEK_PRO,
        with_skills=False,
        repo_root=Path(tmp_path),
        bench_extra_args=[],
        reasoning_effort="xhigh",
    )

    command = captured[0]
    effort_index = command.index("--reasoning-effort")
    assert command[effort_index : effort_index + 2] == [
        "--reasoning-effort",
        "xhigh",
    ]
    assert "BENCHFLOW_PROVIDER_REQUEST_FILTER=reasoning-effort:xhigh" in command


def test_openhands_command_uses_skillsbench_11_runtime_settings():
    task_dir = REPO_ROOT / "tasks" / "jax-computing-basics"

    command = rollout.build_bench_run_command(
        task_dir=task_dir,
        jobs_dir=REPO_ROOT / ".tmp-jobs",
        agent="openhands",
        model=GLM_52,
        with_skills=True,
        bench_extra_args=["--sandbox-user", "none"],
        reasoning_effort="high",
        prompt_prefix="You must use the deployed skill.",
        capture_workspace=True,
        capture_model_io=True,
        skip_verify=True,
    )

    assert command[:5] == ["uv", "run", "bench", "eval", "run"]
    assert command[command.index("--skill-mode") + 1] == "with-skill"
    assert command[command.index("--sandbox-user") + 1] == "agent"
    assert command[command.index("--agent-idle-timeout") + 1] == "0"
    assert command[command.index("--prompt") + 1].startswith("You must use the deployed skill.\n\nGiven a set of tasks")
    assert "--capture-workspace" not in command
    assert "--capture-model-io" in command
    assert "BENCHFLOW_CAPTURE_WORKSPACE=1" in command
    assert "BENCHFLOW_CAPTURE_MODEL_IO=1" not in command
    assert "--skip-verify" not in command
    assert "--reasoning-effort" not in command
