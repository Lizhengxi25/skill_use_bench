"""Persistent harvesting of BenchFlow ACP agent diagnostics."""

from pathlib import Path
from types import SimpleNamespace

from skillsbench_x import rollout


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
        reasoning_effort="high",
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


def test_run_one_forwards_and_harvests_model_io(tmp_path, monkeypatch):
    task_dir = tmp_path / "task-one"
    task_dir.mkdir()
    run_dir = tmp_path / "runs" / "diagnostic"
    rollout_dir = tmp_path / "jobs" / "trial"
    model_io_dir = rollout_dir / "agent" / "model_io"
    model_io_dir.mkdir(parents=True)
    (rollout_dir / "verifier").mkdir()
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
    assert "--capture-model-io" not in captured[0]
    assert captured[0][:5] == ["uv", "run", "bench", "eval", "run"]
    assert (out / "model_io" / "logical_requests.jsonl").read_text() == '{"request": 1}\n'
    assert (out / "model_io" / "provider_http.jsonl").read_text() == '{"body": 1}\n'
    assert (out / "model_io" / "provider_sse.raw").read_bytes() == b"data: response.completed\n\n"
    metadata = (out / "metadata.tsv").read_text()
    assert "capture_model_io\tTrue\n" in metadata
