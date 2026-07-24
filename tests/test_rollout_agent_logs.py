"""Persistent harvesting of BenchFlow ACP agent diagnostics."""

import json
from pathlib import Path
from types import SimpleNamespace

from skillsbench_x import rollout


def _llm_exchange() -> dict:
    return {
        "request": {
            "method": "POST",
            "path": "/v1/chat/completions",
            "body": {
                "model": "test-model",
                "messages": [
                    {"role": "system", "content": "Private harness instructions."},
                    {"role": "user", "content": "Inspect the repository."},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "terminal",
                                    "arguments": '{"command":"ls"}',
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_1",
                        "content": "README.md\nsrc",
                    },
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "terminal",
                            "description": "Run a command.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "command": {"type": "string"},
                                },
                            },
                        },
                    }
                ],
            },
        },
        "response": {
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Repository inspected.",
                        }
                    }
                ]
            },
        },
    }


def test_benchflow_llm_renderer_preserves_tool_io_and_omits_system(tmp_path):
    path = tmp_path / "llm_trajectory.jsonl"
    path.write_text(json.dumps(_llm_exchange()) + "\n")

    rendered = rollout.benchflow_llm_jsonl_to_text(path)

    assert "BenchFlow's structured provider-boundary" in rendered
    assert "Inspect the repository." in rendered
    assert "name:    terminal" in rendered
    assert "args:    ls" in rendered
    assert "output:  README.md\nsrc" in rendered
    assert "Repository inspected." in rendered
    assert "Private harness instructions." not in rendered


def test_run_one_harvests_agent_stdout_and_stderr_logs(tmp_path, monkeypatch):
    task_dir = tmp_path / "task-one"
    task_dir.mkdir()
    run_dir = tmp_path / "runs" / "diagnostic"
    rollout_dir = tmp_path / "jobs" / "trial"
    agent_dir = rollout_dir / "agent"
    agent_dir.mkdir(parents=True)
    (rollout_dir / "verifier").mkdir()
    result_text = '{"error": "failed", "error_data": {"message": "provider"}}'
    (rollout_dir / "result.json").write_text(result_text)
    (agent_dir / "codex_acp.txt").write_text("non-protocol stdout\n")
    (agent_dir / "codex_acp.stderr.txt").write_text("provider error detail\n")

    monkeypatch.setattr(
        rollout.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(rollout, "locate_rollout_dir", lambda *args: rollout_dir)

    task_id, rc, _ = rollout.run_one(
        task_dir=task_dir,
        run_dir=run_dir,
        agent="codex-acp",
        model="openrouter/minimax/minimax-m3",
        with_skills=False,
        repo_root=Path(tmp_path),
        bench_extra_args=[],
        # Keep this log-harvesting test independent of provider reasoning
        # controls; those are covered by the model-profile tests.
        reasoning_effort=None,
        prompt_prefix="",
        prompt_note="",
        capture_workspace=False,
        capture_model_io=False,
        skip_verify=True,
    )

    out = run_dir / task_id
    assert rc == 1
    assert (out / "result.json").read_text() == result_text
    assert (out / "codex_acp.txt").read_text() == "non-protocol stdout\n"
    assert (out / "codex_acp.stderr.txt").read_text() == "provider error detail\n"


def test_run_one_prefers_and_harvests_structured_llm_trajectory(
    tmp_path,
    monkeypatch,
):
    task_dir = tmp_path / "task-one"
    task_dir.mkdir()
    run_dir = tmp_path / "runs" / "diagnostic"
    rollout_dir = tmp_path / "jobs" / "trial"
    (rollout_dir / "agent").mkdir(parents=True)
    (rollout_dir / "verifier").mkdir()
    trajectory_dir = rollout_dir / "trajectory"
    trajectory_dir.mkdir()
    llm_jsonl = json.dumps(_llm_exchange()) + "\n"
    (trajectory_dir / "llm_trajectory.jsonl").write_text(llm_jsonl)
    (trajectory_dir / "acp_trajectory.jsonl").write_text('{"type":"agent_message","text":"lossy fallback"}\n')

    monkeypatch.setattr(
        rollout.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(rollout, "locate_rollout_dir", lambda *args: rollout_dir)

    task_id, rc, note = rollout.run_one(
        task_dir=task_dir,
        run_dir=run_dir,
        agent="codex-acp",
        model="gpt-5.5",
        with_skills=False,
        repo_root=Path(tmp_path),
        bench_extra_args=[],
        reasoning_effort="xhigh",
        prompt_prefix="",
        prompt_note="",
        capture_workspace=False,
        capture_model_io=False,
        skip_verify=True,
    )

    out = run_dir / task_id
    assert rc == 0
    assert "structured provider exchanges" in note
    assert "README.md\nsrc" in (out / "trajectory.log").read_text()
    assert "lossy fallback" not in (out / "trajectory.log").read_text()
    assert (out / "trajectory.jsonl").read_text() == llm_jsonl
    assert (out / "llm_trajectory.jsonl").read_text() == llm_jsonl


def test_run_one_forwards_and_harvests_model_io(tmp_path, monkeypatch):
    task_dir = tmp_path / "task-one"
    task_dir.mkdir()
    run_dir = tmp_path / "runs" / "diagnostic"
    rollout_dir = tmp_path / "jobs" / "trial"
    model_io_dir = rollout_dir / "agent" / "model_io"
    model_io_dir.mkdir(parents=True)
    (rollout_dir / "verifier").mkdir()
    trajectory_dir = rollout_dir / "trajectory"
    trajectory_dir.mkdir()
    (trajectory_dir / "llm_trajectory.jsonl").write_text(json.dumps(_llm_exchange()) + "\n")
    (model_io_dir / "logical_requests.jsonl").write_text('{"request": 1}\n')
    (model_io_dir / "provider_http.jsonl").write_text('{"body": 1}\n')
    (model_io_dir / "provider_sse.raw").write_bytes(b"data: response.completed\n\n")
    captured: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        captured.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)
    monkeypatch.setattr(rollout, "locate_rollout_dir", lambda *args: rollout_dir)

    task_id, rc, _ = rollout.run_one(
        task_dir=task_dir,
        run_dir=run_dir,
        agent="codex-acp",
        model="openrouter/tencent/hy3",
        with_skills=False,
        repo_root=Path(tmp_path),
        bench_extra_args=[],
        reasoning_effort="none",
        prompt_prefix="",
        prompt_note="",
        capture_workspace=False,
        capture_model_io=True,
        skip_verify=True,
    )

    out = run_dir / task_id
    assert rc == 0
    assert "--capture-model-io" in captured[0]
    assert "BENCHFLOW_CAPTURE_MODEL_IO=1" not in captured[0]
    assert captured[0][:5] == ["uv", "run", "bench", "eval", "run"]
    assert (out / "model_io" / "logical_requests.jsonl").read_text() == '{"request": 1}\n'
    assert (out / "model_io" / "provider_http.jsonl").read_text() == '{"body": 1}\n'
    assert (out / "model_io" / "provider_sse.raw").read_bytes() == b"data: response.completed\n\n"
    assert (out / "agent" / "model_io" / "logical_requests.jsonl").read_text() == '{"request": 1}\n'
    metadata = (out / "metadata.tsv").read_text()
    assert "capture_model_io\tTrue\n" in metadata


def test_run_one_rejects_stale_trajectory_when_current_attempt_has_none(
    tmp_path,
    monkeypatch,
):
    """Regression: an artifact-less rerun must not reuse the prior trajectory."""
    task_dir = tmp_path / "task-one"
    task_dir.mkdir()
    run_dir = tmp_path / "runs" / "diagnostic"
    out = run_dir / task_dir.name
    out.mkdir(parents=True)
    stale_trajectory = '{"type":"agent_message","text":"previous attempt"}\n'
    (out / "trajectory.jsonl").write_text(stale_trajectory)
    (out / "exit_code.txt").write_text("0\n")

    rollout_dir = tmp_path / "jobs" / "trial"
    (rollout_dir / "agent").mkdir(parents=True)
    (rollout_dir / "verifier").mkdir()
    monkeypatch.setattr(
        rollout.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(rollout, "locate_rollout_dir", lambda *args: rollout_dir)

    task_id, rc, note = rollout.run_one(
        task_dir=task_dir,
        run_dir=run_dir,
        agent="codex-acp",
        model="gpt-5.5",
        with_skills=False,
        repo_root=Path(tmp_path),
        bench_extra_args=[],
        reasoning_effort="xhigh",
    )

    assert task_id == "task-one"
    assert rc == rollout.MISSING_TRAJECTORY_EXIT_CODE
    assert "this attempt harvested no trajectory.jsonl" in note
    assert (out / "exit_code.txt").read_text() == (f"{rollout.MISSING_TRAJECTORY_EXIT_CODE}\n")
    assert (out / "trajectory.jsonl").read_text() == stale_trajectory


def test_run_one_persists_complete_debug_artifact_whitelist(
    tmp_path,
    monkeypatch,
):
    task_dir = tmp_path / "task-one"
    task_dir.mkdir()
    run_dir = tmp_path / "runs" / "diagnostic"
    rollout_dir = tmp_path / "jobs" / "trial"
    agent_dir = rollout_dir / "agent"
    trajectory_dir = rollout_dir / "trajectory"
    verifier_dir = rollout_dir / "verifier"
    artifacts_dir = rollout_dir / "artifacts"
    workspace_dir = rollout_dir / "workspace"
    for directory in (
        agent_dir,
        trajectory_dir,
        verifier_dir,
        artifacts_dir,
        workspace_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    (rollout_dir / "config.json").write_text('{"agent":"claude-agent-acp"}\n')
    (rollout_dir / "timing.json").write_text('{"agent":1.0}\n')
    (rollout_dir / "prompts.json").write_text('["inspect"]\n')
    (rollout_dir / "result.json").write_text('{"error":null}\n')
    (agent_dir / "claude_agent_acp.txt").write_text("agent stdout\n")
    (agent_dir / "claude_agent_acp.stderr.txt").write_text("agent stderr\n")
    (agent_dir / "acp_wire.jsonl").write_text('{"direction":"agent_to_client"}\n')
    model_io_dir = agent_dir / "model_io"
    model_io_dir.mkdir()
    (model_io_dir / "provider_request.body").write_text('{"model":"hy3"}\n')
    llm_jsonl = json.dumps(_llm_exchange()) + "\n"
    (trajectory_dir / "llm_trajectory.jsonl").write_text(llm_jsonl)
    acp_jsonl = '{"type":"agent_message","text":"done"}\n'
    (trajectory_dir / "acp_trajectory.jsonl").write_text(acp_jsonl)
    (verifier_dir / "reward.txt").write_text("1\n")
    (verifier_dir / "test-stdout.txt").write_text("passed\n")
    (artifacts_dir / "workspace.tgz").write_bytes(b"workspace archive")
    (artifacts_dir / "debug.txt").write_text("artifact\n")
    (workspace_dir / "state.txt").write_text("final workspace\n")

    monkeypatch.setattr(
        rollout.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(rollout, "locate_rollout_dir", lambda *args: rollout_dir)

    task_id, rc, _ = rollout.run_one(
        task_dir=task_dir,
        run_dir=run_dir,
        agent="claude-agent-acp",
        model="openrouter/tencent/hy3",
        with_skills=False,
        repo_root=Path(tmp_path),
        bench_extra_args=[],
        reasoning_effort=None,
        capture_workspace=True,
        capture_model_io=True,
    )

    out = run_dir / task_id
    assert rc == 0
    assert (out / "config.json").read_text() == '{"agent":"claude-agent-acp"}\n'
    assert (out / "timing.json").read_text() == '{"agent":1.0}\n'
    assert (out / "prompts.json").read_text() == '["inspect"]\n'
    assert (out / "trajectory" / "acp_trajectory.jsonl").read_text() == acp_jsonl
    assert (out / "trajectory" / "llm_trajectory.jsonl").read_text() == llm_jsonl
    assert (out / "agent" / "acp_wire.jsonl").read_text() == '{"direction":"agent_to_client"}\n'
    assert (out / "agent" / "model_io" / "provider_request.body").read_text() == '{"model":"hy3"}\n'
    assert (out / "verifier" / "test-stdout.txt").read_text() == "passed\n"
    assert (out / "artifacts" / "debug.txt").read_text() == "artifact\n"
    assert (out / "artifacts" / "workspace.tgz").read_bytes() == b"workspace archive"
    assert (out / "workspace" / "state.txt").read_text() == "final workspace\n"

    # Existing top-level compatibility artifacts remain available.
    assert (out / "result.json").read_text() == '{"error":null}\n'
    assert (out / "trajectory.jsonl").read_text() == llm_jsonl
    assert (out / "llm_trajectory.jsonl").read_text() == llm_jsonl
    assert (out / "reward.txt").read_text() == "1\n"
    assert (out / "workspace.tgz").read_bytes() == b"workspace archive"
    assert (out / "claude_agent_acp.txt").read_text() == "agent stdout\n"
    assert (out / "claude_agent_acp.stderr.txt").read_text() == "agent stderr\n"
    assert (out / "model_io" / "provider_request.body").read_text() == '{"model":"hy3"}\n'


def test_debug_harvest_failures_do_not_change_success_return_code(
    tmp_path,
    monkeypatch,
    capsys,
):
    task_dir = tmp_path / "task-one"
    task_dir.mkdir()
    run_dir = tmp_path / "runs" / "diagnostic"
    rollout_dir = tmp_path / "jobs" / "trial"
    (rollout_dir / "agent").mkdir(parents=True)
    trajectory_dir = rollout_dir / "trajectory"
    trajectory_dir.mkdir()
    (trajectory_dir / "llm_trajectory.jsonl").write_text(json.dumps(_llm_exchange()) + "\n")
    verifier_dir = rollout_dir / "verifier"
    verifier_dir.mkdir()
    (rollout_dir / "result.json").write_text('{"error":null}\n')
    failed_copy_source = verifier_dir / "debug.txt"
    failed_copy_source.write_text("debug\n")

    monkeypatch.setattr(
        rollout.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(rollout, "locate_rollout_dir", lambda *args: rollout_dir)

    out = run_dir / task_dir.name
    failed_tree_destination = out / "agent"
    real_mkdir = Path.mkdir

    def fake_mkdir(path, *args, **kwargs):
        if path == failed_tree_destination:
            raise PermissionError("simulated tree destination failure")
        return real_mkdir(path, *args, **kwargs)

    real_copy2 = rollout.shutil.copy2

    def fake_copy2(source, destination, *args, **kwargs):
        if Path(source) == failed_copy_source:
            raise OSError("simulated file copy failure")
        return real_copy2(source, destination, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    monkeypatch.setattr(rollout.shutil, "copy2", fake_copy2)

    task_id, rc, _ = rollout.run_one(
        task_dir=task_dir,
        run_dir=run_dir,
        agent="claude-agent-acp",
        model="openrouter/tencent/hy3",
        with_skills=False,
        repo_root=Path(tmp_path),
        bench_extra_args=[],
    )

    assert task_id == "task-one"
    assert rc == 0
    assert (out / "exit_code.txt").read_text() == "0\n"
    assert (out / "result.json").read_text() == '{"error":null}\n'

    diagnostics = (out / "debug_harvest_errors.log").read_text()
    assert "create tree destination" in diagnostics
    assert "simulated tree destination failure" in diagnostics
    assert "copy file" in diagnostics
    assert "simulated file copy failure" in diagnostics
    assert "debug_harvest_error_count\t2\n" in (out / "metadata.tsv").read_text()
    assert "WARNING: debug artifact harvest failed" in capsys.readouterr().err
