#!/usr/bin/env python3
"""LLM-as-judge driver: score a rollout against per-task phased rubrics.

Reads runs/<run_id>/<task_id>/trajectory.log and tasks/<task_id>/rubric/judge_phase_*.md;
writes grades/<grade_id>/<task_id>/judge_phase_*.json.

Mirrors test/group1/with_skills/lave_cdx_low/run_codex_judges.sh — same prompt
preamble, same per-phase output filenames, same per-task layout.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

PHASES = ("skill_identification", "module_sequence", "post_processing")

JUDGE_PREAMBLE = (
    "You are a Codex judge agent. Use the grading rubric below to evaluate the trajectory.\n\n"
    "The trajectory file to evaluate is trajectory.log in the current working directory. "
    "Read trajectory.log yourself. Do not read or use any rubric.json file. "
    "Return exactly one JSON object matching the rubric output schema, with no Markdown fences "
    "and no surrounding text."
)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_one_judge(grade_root: Path, task_id: str, phase: str,
                  rubric_md: Path, trajectory_src: Path,
                  model_effort: str, skip_existing: bool = False) -> tuple[str, str, int, str]:
    task_grade_dir = grade_root / task_id
    task_grade_dir.mkdir(parents=True, exist_ok=True)

    output_json = task_grade_dir / f"judge_phase_{phase}.json"
    agent_log = task_grade_dir / f"judge_phase_{phase}.agent.log"

    # Default is re-judge + overwrite: rollouts get re-run and re-graded in
    # place, so a previously-written grade is stale and must be replaced.
    # `--skip-existing` (opt-in) keeps the old resume-on-crash behaviour.
    if skip_existing and output_json.exists() and output_json.stat().st_size > 0:
        return task_id, phase, 0, "SKIP"

    # Always refresh the trajectory copy.  A re-run rollout overwrites
    # runs/<run_id>/<task>/trajectory.log, and the judge must score that latest
    # trajectory — not a stale copy left in the grade dir by a previous grading.
    trajectory_dst = task_grade_dir / "trajectory.log"
    shutil.copy2(trajectory_src, trajectory_dst)

    # Drop any stale grade up front so a failed/empty re-judge can't masquerade
    # as a fresh result in grading_summary.json (which only checks presence+size).
    if output_json.exists():
        output_json.unlink()

    rubric_text = rubric_md.read_text()
    prompt = f"{JUDGE_PREAMBLE}\n\nGrading rubric:\n{rubric_text}"

    started_at = utcnow()
    agent_log.write_text(
        f"task_id\t{task_id}\n"
        f"phase\t{phase}\n"
        f"rubric_file\t{rubric_md}\n"
        f"trajectory_file\t{trajectory_dst}\n"
        f"output_json\t{output_json}\n"
        f"started_at_utc\t{started_at}\n"
    )

    cmd = [
        "codex", "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox", "read-only",
        "-c", f"model_reasoning_effort={model_effort}",
        "--output-last-message", str(output_json),
        prompt,
    ]
    with agent_log.open("ab") as lf:
        proc = subprocess.run(cmd, cwd=task_grade_dir, stdout=lf, stderr=subprocess.STDOUT)
    rc = proc.returncode

    ended_at = utcnow()
    with agent_log.open("a") as f:
        f.write(f"ended_at_utc\t{ended_at}\nexit_code\t{rc}\n")

    return task_id, phase, rc, "OK" if rc == 0 else f"FAIL:{rc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-as-judge driver for skill-eval rollouts")
    parser.add_argument("--run", required=True, help="runs/<run_id>/")
    parser.add_argument("--rubric-root", required=True, help="tasks/ root containing <task_id>/rubric/")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--reasoning", default="high")
    parser.add_argument("--grades-root", default="grades")
    parser.add_argument("--grade-id", default=None)
    parser.add_argument("--tasks", default=None, help="comma-separated task IDs to limit to")
    parser.add_argument("--phases", default=None, help="comma-separated phases to limit to")
    parser.add_argument("--skip-existing", action="store_true",
                        help="resume mode: leave already-graded phases untouched. "
                             "Default is to re-judge and overwrite every phase.")
    args = parser.parse_args()

    if not shutil.which("codex"):
        sys.exit("codex CLI not found on PATH")

    run_dir = Path(args.run).resolve()
    rubric_root = Path(args.rubric_root).resolve()

    task_filter = set(args.tasks.split(",")) if args.tasks else None
    phase_filter = set(args.phases.split(",")) if args.phases else set(PHASES)

    task_dirs = sorted(
        p for p in run_dir.iterdir()
        if p.is_dir() and (p / "trajectory.log").exists()
    )
    if task_filter:
        task_dirs = [t for t in task_dirs if t.name in task_filter]
    if not task_dirs:
        sys.exit(f"no task trajectories under {run_dir}")

    grade_id = args.grade_id or (run_dir.name + "--" + utcnow().replace(":", "").replace("-", ""))
    grade_root = Path(args.grades_root).resolve() / grade_id
    grade_root.mkdir(parents=True, exist_ok=True)

    manifest = {
        "grade_id": grade_id,
        "run_dir": str(run_dir),
        "rubric_root": str(rubric_root),
        "phases": sorted(phase_filter),
        "started_at_utc": utcnow(),
    }
    (grade_root / "manifest.json").write_text(json.dumps(manifest, indent=2))

    jobs: list[tuple[str, str, Path, Path]] = []
    for td in task_dirs:
        task_id = td.name
        for phase in PHASES:
            if phase not in phase_filter:
                continue
            rubric_md = rubric_root / task_id / "rubric" / f"judge_phase_{phase}.md"
            if not rubric_md.exists():
                print(f"WARN: missing rubric {rubric_md}", file=sys.stderr)
                continue
            jobs.append((task_id, phase, rubric_md, td / "trajectory.log"))

    print(f"grade_root: {grade_root}")
    print(f"running {len(jobs)} judge job(s) with concurrency {args.concurrency}")

    failures = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {
            ex.submit(run_one_judge, grade_root, t, p, r, src, args.reasoning, args.skip_existing):
                (t, p) for (t, p, r, src) in jobs
        }
        for fut in as_completed(futs):
            task_id, phase, rc, status = fut.result()
            print(f"[{status}] {task_id}/{phase}")
            if rc != 0:
                failures += 1

    summary = {
        "grade_id": grade_id,
        "ended_at_utc": utcnow(),
        "failures": failures,
        "tasks": {},
    }
    for td in task_dirs:
        task_id = td.name
        per_phase = {}
        for phase in PHASES:
            if phase not in phase_filter:
                continue
            jf = grade_root / task_id / f"judge_phase_{phase}.json"
            per_phase[phase] = {
                "output_json_present": jf.exists(),
                "output_json_nonempty": jf.exists() and jf.stat().st_size > 0,
            }
        summary["tasks"][task_id] = per_phase
    (grade_root / "grading_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"summary: {grade_root}/grading_summary.json")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
