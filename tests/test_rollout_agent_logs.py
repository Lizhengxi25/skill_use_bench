"""Persistent harvesting of BenchFlow ACP agent diagnostics."""

from pathlib import Path
from types import SimpleNamespace

from skillsbench_x import rollout


def test_normalize_rootless_podman_ownership_uses_user_namespace(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setenv("CONTAINERS_STORAGE_CONF", "/job/storage.conf")
    monkeypatch.setattr(rollout.shutil, "which", lambda name: "/usr/bin/podman")

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)

    rollout.normalize_rootless_podman_ownership(tmp_path)

    assert calls[0][0] == [
        "/usr/bin/podman",
        "unshare",
        "chown",
        "-R",
        "0:0",
        str(tmp_path),
    ]
    assert calls[0][1]["timeout"] == 120


def test_run_one_harvests_agent_stdout_and_stderr_logs(tmp_path, monkeypatch):
    task_dir = tmp_path / "task-one"
    task_dir.mkdir()
    run_dir = tmp_path / "runs" / "diagnostic"
    stale_out = run_dir / task_dir.name
    stale_out.mkdir(parents=True)
    (stale_out / "trajectory.jsonl").write_text("stale trajectory\n")
    (stale_out / "exit_code.txt").write_text("0\n")
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
        prompt_args=[],
        prompt_note="",
        capture_workspace=False,
        skip_verify=True,
    )

    out = run_dir / task_id
    assert rc == 1
    assert (out / "result.json").read_text() == result_text
    assert (out / "codex_acp.txt").read_text() == "non-protocol stdout\n"
    assert (out / "codex_acp.stderr.txt").read_text() == "provider error detail\n"
    assert not (out / "trajectory.jsonl").exists()
    assert (out / "exit_code.txt").read_text() == "1\n"
