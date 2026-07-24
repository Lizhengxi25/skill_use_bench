import json
from pathlib import Path

from skillsbench_x import run_openrouter_harness_smoke as smoke


def test_matrix_commands_use_default_reasoning_and_debug_capture(tmp_path) -> None:
    experiment = tmp_path / "experiment"
    for model_name, model in smoke.MODELS.items():
        for harness in smoke.HARNESSES:
            for arm in smoke.ARMS:
                spec = smoke.build_arm_spec(
                    experiment_dir=experiment,
                    model_name=model_name,
                    model=model,
                    harness=harness,
                    task_name="rails-dev",
                    arm=arm,
                )
                command = list(spec.command)
                assert "--reasoning" not in command
                assert "--reasoning-effort" not in command
                assert "--capture-model-io" in command
                assert "--capture-workspace" in command


def test_matrix_dry_run_prints_all_possible_arms_without_writes(tmp_path: Path, capsys) -> None:
    output_root = tmp_path / "runs"
    result = smoke.main(
        [
            "--output-root",
            str(output_root),
            "--experiment-id",
            "dry-run-sentinel",
            "--dry-run",
        ]
    )

    assert result == 0
    assert not output_root.exists()
    lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("DRY-RUN ") and ("/no_skill:" in line or "/with_skill:" in line)
    ]
    assert len(lines) == 36


def test_matrix_dry_run_can_select_one_diagnostic_rollout(tmp_path: Path, capsys) -> None:
    output_root = tmp_path / "runs"
    result = smoke.main(
        [
            "--output-root",
            str(output_root),
            "--experiment-id",
            "one-rollout",
            "--models",
            "hy3",
            "--harnesses",
            "openhands",
            "--arms",
            "no_skill",
            "--stage1-only",
            "--dry-run",
        ]
    )

    assert result == 0
    assert not output_root.exists()
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("DRY-RUN ") and "/no_skill:" in line]
    assert len(lines) == 1
    assert "hy3/openhands/rails-dev/no_skill" in lines[0]


def test_matrix_dry_run_can_resume_at_stage2_without_repeating_stage1(tmp_path: Path, capsys) -> None:
    output_root = tmp_path / "runs"
    result = smoke.main(
        [
            "--output-root",
            str(output_root),
            "--experiment-id",
            "stage-two-only",
            "--models",
            "hy3",
            "--harnesses",
            "claude-code",
            "--arms",
            "with_skill",
            "--stage2-only",
            "--dry-run",
        ]
    )

    assert result == 0
    assert not output_root.exists()
    output = capsys.readouterr().out
    assert "hy3/claude-code/rust-router/with_skill" in output
    assert "/rails-dev/" not in output


def test_reasoning_validator_ignores_replayed_model_thinking_content() -> None:
    payload = {
        "model": "tencent/hy3",
        "messages": [
            {
                "role": "assistant",
                "thinking_blocks": [{"type": "thinking", "thinking": "prior output"}],
                "reasoning_content": "prior output",
            }
        ],
    }

    assert smoke._reasoning_control_fields(payload) == []


def test_reasoning_validator_detects_request_level_controls() -> None:
    payload = {
        "reasoning_effort": "high",
        "include": ["reasoning.encrypted_content"],
        "output_config": {"effort": "high"},
        "extra_body": {"reasoning": {"effort": "high"}},
    }

    assert smoke._reasoning_control_fields(payload) == [
        "reasoning_effort",
        "include",
        "output_config.effort",
        "extra_body.reasoning",
    ]


def test_with_skill_prompts_use_each_harness_discovery_path() -> None:
    codex_prompt = smoke.WITH_SKILL_PROMPTS["codex"].read_text()
    claude_prompt = smoke.WITH_SKILL_PROMPTS["claude-code"].read_text()
    openhands_prompt = smoke.WITH_SKILL_PROMPTS["openhands"].read_text()

    assert "`$HOME/.agents/skills/`" in codex_prompt
    assert "`$HOME/.claude/skills/`" in claude_prompt
    assert "`/app/.agents/skills/`" in openhands_prompt


def test_completed_skill_read_requires_task_path_and_success(tmp_path: Path) -> None:
    wire = tmp_path / "agent.acp_wire.jsonl"
    records = [
        {
            "direction": "agent_to_client",
            "message": {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "failed-directory",
                        "rawInput": {"file_path": "/app/.agents/skills/"},
                    }
                },
            },
        },
        {
            "direction": "agent_to_client",
            "message": {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "read-skill",
                        "rawInput": {
                            "file_path": "/skills/rails-dev/SKILL.md",
                        },
                    }
                },
            },
        },
        {
            "direction": "agent_to_client",
            "message": {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "read-skill",
                        "status": "completed",
                        "rawOutput": "skill contents",
                    }
                },
            },
        },
    ]
    wire.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    assert smoke._has_completed_skill_read([wire], task_name="rails-dev")
    assert not smoke._has_completed_skill_read([wire], task_name="rust-router")


def test_cell_records_launcher_exceptions_instead_of_aborting_manifest(tmp_path: Path, monkeypatch) -> None:
    def fail_arm(*_args, **_kwargs):
        raise RuntimeError("synthetic launcher failure")

    monkeypatch.setattr(smoke, "_run_arm", fail_arm)
    result = smoke._run_cell(
        experiment_dir=tmp_path,
        jobs_root=tmp_path / "raw_jobs",
        model_name="hy3",
        model=smoke.MODELS["hy3"],
        harness="claude-code",
        task_name="rails-dev",
        timeout_sec=1,
    )

    assert result["status"] == "failed"
    assert len(result["arms"]) == 2
    assert all(item["return_code"] == 1 for item in result["arms"])
    assert all(item["errors"] == ["launcher exception: RuntimeError: synthetic launcher failure"] for item in result["arms"])


def test_arm_validator_requires_complete_provider_capture(tmp_path: Path) -> None:
    spec = smoke.build_arm_spec(
        experiment_dir=tmp_path,
        model_name="hy3",
        model=smoke.MODELS["hy3"],
        harness="openhands",
        task_name="rails-dev",
        arm="no_skill",
    )
    leaf = spec.run_leaf
    (leaf / "agent").mkdir(parents=True)
    request = leaf / "model_io" / "000001"
    request.mkdir(parents=True)

    (leaf / "exit_code.txt").write_text("0\n")
    (leaf / "result.json").write_text(
        json.dumps(
            {
                "error": None,
                "partial_trajectory": False,
                "capture_model_io": True,
            }
        )
    )
    (leaf / "config.json").write_text(json.dumps({"capture_model_io": True}))
    for filename in (
        "trajectory.jsonl",
        "trajectory.log",
        "llm_trajectory.jsonl",
        "timing.json",
        "prompts.json",
        "workspace.tgz",
    ):
        (leaf / filename).write_text("captured\n")
    (leaf / "agent" / "openhands.acp_wire.jsonl").write_text("{}\n")
    litellm_debug = leaf / "agent" / "litellm" / "session-test"
    litellm_debug.mkdir(parents=True)
    for filename in ("stdout.log", "stderr.log", "callback.jsonl"):
        (litellm_debug / filename).write_text("captured\n")
    (request / "logical_request.body").write_text("{}")
    (request / "logical_request.json").write_text("{}")
    (request / "provider_request.body").write_text("{}")
    (request / "provider_request.json").write_text("{}")
    (request / "provider_response.sse").write_text("data: [DONE]\n\n")
    (request / "metadata.json").write_text(
        json.dumps(
            {
                "transport_complete": True,
                "error": None,
                "request": {
                    "url": "https://openrouter.ai/api/v1/chat/completions",
                },
                "response": {
                    "status": 200,
                    "file": "provider_response.sse",
                },
            }
        )
    )

    assert smoke.validate_arm(spec, 0) == (True, [])

    (litellm_debug / "stdout.log").write_text('"POST /v1/messages/count_tokens?beta=true HTTP/1.1" 200 OK\n')
    (litellm_debug / "stderr.log").write_text("Provider token counting failed (404)\nFalling back to local tokenizer.\n")
    (request / "metadata.json").write_text(
        json.dumps(
            {
                "transport_complete": True,
                "error": None,
                "request": {
                    "url": "https://openrouter.ai/api/v1/responses/input_tokens",
                },
                "response": {
                    "status": 404,
                    "file": "provider_response.sse",
                },
            }
        )
    )
    assert smoke.validate_arm(spec, 0) == (True, [])

    (litellm_debug / "stdout.log").write_text("")
    assert smoke.validate_arm(spec, 0) == (False, ["000001: provider HTTP status 404"])
