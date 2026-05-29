#!/usr/bin/env python3
"""Rollout driver: stage a workdir per task, run the agent, capture trajectory.

Mirrors test/group1/with_skills/lave_cdx_low/run_codex_agents.sh in behavior,
but reads the Harbor-shaped tasks/<task_id>/ layout instead of the flat
{run_env,user_query,rubrics}/<skill_name>/ layout.

No Docker yet — the agent runs directly on the host with codex's own sandbox.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

HARNESS_SKILL_MOUNT = {
    "claude-code": ".claude/skills",
    "codex": ".agents/skills",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stage_workdir(task_dir: Path, workdir: Path, harness: str, with_skills: bool) -> None:
    """Copy environment/files/* into workdir/, plus skills/ at the harness-specific mount path."""
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    files_src = task_dir / "environment" / "files"
    for entry in files_src.iterdir():
        dst = workdir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dst)
        else:
            shutil.copy2(entry, dst)

    if with_skills:
        mount_rel = HARNESS_SKILL_MOUNT[harness]
        mount_dir = workdir / mount_rel
        mount_dir.mkdir(parents=True)
        skills_src = task_dir / "environment" / "skills"
        if skills_src.exists():
            for skill in skills_src.iterdir():
                if skill.is_dir():
                    shutil.copytree(skill, mount_dir / skill.name)


def build_codex_cmd(prompt: str, model_reasoning_effort: str) -> list[str]:
    return [
        "codex", "exec",
        "--skip-git-repo-check",
        "--sandbox", "workspace-write",
        "-c", f"model_reasoning_effort={model_reasoning_effort}",
        prompt,
    ]


def build_claude_cmd(prompt: str) -> list[str]:
    # placeholder — claude-code CLI invocation is a follow-up
    return ["claude", "-p", prompt]


def run_one(task_dir: Path, run_dir: Path, harness: str,
            model_reasoning_effort: str, with_skills: bool) -> tuple[str, int]:
    task_id = task_dir.name
    out = run_dir / task_id
    out.mkdir(parents=True, exist_ok=True)
    workdir = out / "workdir"

    stage_workdir(task_dir, workdir, harness, with_skills)

    prompt = (task_dir / "instruction.md").read_text()

    started_at = utcnow()
    metadata = out / "metadata.tsv"
    metadata.write_text(
        f"task_id\t{task_id}\n"
        f"harness\t{harness}\n"
        f"with_skills\t{with_skills}\n"
        f"reasoning\t{model_reasoning_effort}\n"
        f"workdir\t{workdir}\n"
        f"started_at_utc\t{started_at}\n"
    )

    if harness == "codex":
        cmd = build_codex_cmd(prompt, model_reasoning_effort)
    elif harness == "claude-code":
        cmd = build_claude_cmd(prompt)
    else:
        raise ValueError(f"unknown harness: {harness}")

    log_path = out / "trajectory.log"
    with log_path.open("wb") as lf:
        proc = subprocess.run(cmd, cwd=workdir, stdout=lf, stderr=subprocess.STDOUT)
    rc = proc.returncode

    ended_at = utcnow()
    with metadata.open("a") as mf:
        mf.write(f"ended_at_utc\t{ended_at}\nexit_code\t{rc}\n")
    (out / "exit_code.txt").write_text(f"{rc}\n")

    return task_id, rc


def discover_tasks(tasks_arg: Path) -> list[Path]:
    if (tasks_arg / "task.toml").exists():
        return [tasks_arg]
    return sorted(
        p for p in tasks_arg.iterdir()
        if p.is_dir() and (p / "task.toml").exists()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Rollout driver for skill-eval tasks")
    parser.add_argument("--tasks", required=True, help="Path to tasks/ root or a single tasks/<id>/")
    parser.add_argument("--harness", choices=["codex", "claude-code"], default="codex")
    parser.add_argument("--reasoning", default="low")
    parser.add_argument("--with-skills", action="store_true", help="Mount skills/ inside the workdir")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--runs-root", default="runs")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    tasks_arg = Path(args.tasks).resolve()
    task_dirs = discover_tasks(tasks_arg)
    if not task_dirs:
        sys.exit(f"no tasks found under {tasks_arg}")

    if not shutil.which({"codex": "codex", "claude-code": "claude"}[args.harness]):
        sys.exit(f"required CLI for harness '{args.harness}' not on PATH")

    run_id = args.run_id or (utcnow().replace(":", "").replace("-", "") + f"-{os.getpid()}")
    run_dir = Path(args.runs_root).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "run_id": run_id,
        "harness": args.harness,
        "reasoning": args.reasoning,
        "with_skills": args.with_skills,
        "task_ids": [t.name for t in task_dirs],
        "started_at_utc": utcnow(),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"run_dir: {run_dir}")
    print(f"running {len(task_dirs)} task(s) with concurrency {args.concurrency}")

    failures = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {
            ex.submit(run_one, t, run_dir, args.harness, args.reasoning, args.with_skills): t.name
            for t in task_dirs
        }
        for fut in as_completed(futs):
            task_id, rc = fut.result()
            tag = "OK" if rc == 0 else f"FAIL:{rc}"
            print(f"[{tag}] {task_id}")
            if rc != 0:
                failures += 1

    manifest["ended_at_utc"] = utcnow()
    manifest["failures"] = failures
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
