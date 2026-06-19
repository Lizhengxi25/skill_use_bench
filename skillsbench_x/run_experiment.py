#!/usr/bin/env python3
"""One-click YAML-driven experiment orchestrator.

Reads a small YAML that describes the experiment matrix (harness × with-skills
× model × …) and dispatches rollout → judge → aggregate for each row via the
existing skillsbench_x/* scripts.  Supports stage skipping so a rubric edit
only triggers `--only judge,aggregate`.

Usage:
    python3 skillsbench_x/run_experiment.py --config experiments/configs/skill-eval/group1-codex.yaml
    python3 skillsbench_x/run_experiment.py --config <yaml> --dry-run
    python3 skillsbench_x/run_experiment.py --config <yaml> --only judge,aggregate
    python3 skillsbench_x/run_experiment.py --config <yaml> --only rollout --rows 0,2

See experiments/configs/skill-eval/group1-codex.yaml for the schema.

Notes
-----
* ``aggregate.format: md``/``json`` emit a default output path under the grade
  dir (``aggregate.md`` / ``aggregate.json``) when the YAML doesn't supply one,
  so ``build_aggregate_cmd`` never leaves ``--md``/``--json`` valueless (which
  previously swallowed the next flag and failed the aggregate stage).  Override
  with ``aggregate.md_path`` / ``aggregate.json_path``.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {
    "rollout":   REPO_ROOT / "skillsbench_x" / "rollout.py",
    "judge":     REPO_ROOT / "skillsbench_x" / "judge.py",
    "aggregate": REPO_ROOT / "skillsbench_x" / "aggregate.py",
}
ALL_STAGES = ("rollout", "judge", "aggregate")


def resolve(path_str: str) -> Path:
    """Treat config-relative paths as relative to the repo root."""
    p = Path(path_str)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p


def render_template(template: str, **fields) -> str:
    try:
        return template.format(**fields)
    except KeyError as e:
        sys.exit(f"unknown placeholder {e} in template: {template!r}")


def build_rollout_cmd(row: dict, cfg: dict, run_id: str) -> list[str]:
    cmd: list[str] = [
        sys.executable, str(SCRIPTS["rollout"]),
        "--tasks", str(resolve(cfg["tasks_root"])),
        "--harness", row["harness"],
        "--run-id", run_id,
        "--runs-root", str(resolve(cfg.get("runs_root", "runs"))),
    ]
    if row.get("with_skills"):
        cmd.append("--with-skills")
    if row.get("model"):
        cmd += ["--model", row["model"]]
    if row.get("agent"):
        cmd += ["--agent", row["agent"]]
    if row.get("reasoning"):
        cmd += ["--reasoning", row["reasoning"]]
    # Pre-query prompt (config-level): file wins over inline. Forwarded to
    # rollout.py → `bench run` → _resolve_prompts (prepended before the query).
    if cfg.get("prompt_file"):
        cmd += ["--prompt-file", str(resolve(cfg["prompt_file"]))]
    elif cfg.get("prompt"):
        cmd += ["--prompt", str(cfg["prompt"])]
    rollout = cfg.get("rollout", {})
    if rollout.get("concurrency"):
        cmd += ["--concurrency", str(rollout["concurrency"])]
    if rollout.get("capture_workspace"):
        cmd.append("--capture-workspace")
    if rollout.get("skip_verify"):
        cmd.append("--skip-verify")

    # Forward extra args to `bench run` after a `--` separator.
    extra = rollout.get("bench_extra_args") or []
    if isinstance(extra, str):
        extra = shlex.split(extra)
    if extra:
        cmd.append("--")
        cmd += [str(x) for x in extra]
    return cmd


def build_judge_cmd(row: dict, cfg: dict, run_id: str, grade_id: str) -> list[str]:
    cmd: list[str] = [
        sys.executable, str(SCRIPTS["judge"]),
        "--run", str(resolve(cfg.get("runs_root", "runs")) / run_id),
        "--rubric-root", str(resolve(cfg["tasks_root"])),
        "--grade-id", grade_id,
        "--grades-root", str(resolve(cfg.get("grades_root", "grades"))),
    ]
    judge = cfg.get("judge", {})
    if judge.get("concurrency"):
        cmd += ["--concurrency", str(judge["concurrency"])]
    if judge.get("reasoning"):
        cmd += ["--reasoning", judge["reasoning"]]
    # Default is re-judge + overwrite; opt into resume-on-crash with
    # `judge.skip_existing: true` (leaves already-graded phases untouched).
    if judge.get("skip_existing"):
        cmd.append("--skip-existing")
    return cmd


def build_aggregate_cmd(row: dict, cfg: dict, grade_id: str) -> list[str]:
    grade_dir = resolve(cfg.get("grades_root", "grades")) / grade_id
    cmd: list[str] = [
        sys.executable, str(SCRIPTS["aggregate"]),
        "--grades", str(grade_dir),
    ]
    agg = cfg.get("aggregate", {})
    fmt = agg.get("format")
    # aggregate.py's --md / --json both REQUIRE a path argument; emit a default
    # under the grade dir when the YAML doesn't give one, so the flag is never
    # left valueless (which would swallow the following flag and fail the stage).
    if fmt == "md":
        out = agg.get("md_path")
        cmd += ["--md", str(out) if out else str(grade_dir / "aggregate.md")]
    elif fmt == "json":
        out = agg.get("json_path")
        cmd += ["--json", str(out) if out else str(grade_dir / "aggregate.json")]
    if agg.get("per_task"):
        cmd.append("--per-task")
    return cmd


def expand_row(row: dict, cfg: dict) -> tuple[str, str]:
    """Compute (run_id, grade_id) for a matrix row using the templates."""
    fields = {
        "name":     cfg["name"],
        "harness":  row["harness"],
        "skills":   "with" if row.get("with_skills") else "no",
        "model":    (row.get("model") or "default").replace("/", "_"),
        "reasoning": row.get("reasoning") or "default",
        "rev":      str(cfg.get("rev", 1)),
    }
    run_id = render_template(
        cfg.get("run_id_template", "{name}-{harness}-{skills}"),
        **fields,
    )
    fields["run_id"] = run_id
    grade_id = render_template(
        cfg.get("grade_id_template", "{run_id}--rev{rev}"),
        **fields,
    )
    return run_id, grade_id


def run_stage(stage: str, cmd: list[str], dry_run: bool) -> int:
    print(f"\n>>> [{stage}] {' '.join(shlex.quote(c) for c in cmd)}")
    if dry_run:
        return 0
    proc = subprocess.run(cmd)
    return proc.returncode


def parse_stage_filter(text: str | None) -> tuple[str, ...]:
    if not text:
        return ALL_STAGES
    wanted = [s.strip() for s in text.split(",") if s.strip()]
    for s in wanted:
        if s not in ALL_STAGES:
            sys.exit(f"unknown stage: {s} (allowed: {','.join(ALL_STAGES)})")
    return tuple(wanted)


def parse_row_filter(text: str | None, n_rows: int) -> set[int] | None:
    if not text:
        return None
    out = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif chunk:
            out.add(int(chunk))
    for i in out:
        if not (0 <= i < n_rows):
            sys.exit(f"row index {i} out of range 0..{n_rows-1}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="YAML-driven experiment orchestrator")
    parser.add_argument("--config", required=True, help="path to experiment YAML")
    parser.add_argument("--only", default=None,
                        help="comma list of stages to run (default: all). "
                             "Example: --only judge,aggregate")
    parser.add_argument("--rows", default=None,
                        help="comma list/ranges of matrix-row indices to run. "
                             "Example: --rows 0,2-3")
    parser.add_argument("--dry-run", action="store_true",
                        help="print commands without executing")
    parser.add_argument("--keep-going", action="store_true",
                        help="continue subsequent stages/rows even if one fails")
    args = parser.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg = yaml.safe_load(cfg_path.read_text())
    if not isinstance(cfg, dict):
        sys.exit(f"config root must be a mapping: {cfg_path}")
    for required in ("name", "tasks_root", "matrix"):
        if required not in cfg:
            sys.exit(f"missing required key {required!r} in {cfg_path}")
    matrix = cfg["matrix"]
    if not isinstance(matrix, list) or not matrix:
        sys.exit("`matrix` must be a non-empty list")

    stages = parse_stage_filter(args.only)
    row_filter = parse_row_filter(args.rows, len(matrix))

    print(f"experiment: {cfg['name']}")
    print(f"config:     {cfg_path}")
    print(f"stages:     {','.join(stages)}")
    print(f"rows:       {sorted(row_filter) if row_filter is not None else f'all ({len(matrix)})'}")
    if args.dry_run:
        print("(dry-run — no commands executed)")

    failed = 0
    for i, row in enumerate(matrix):
        if row_filter is not None and i not in row_filter:
            continue
        if not isinstance(row, dict) or "harness" not in row:
            sys.exit(f"matrix row {i} missing required `harness` key: {row}")

        run_id, grade_id = expand_row(row, cfg)
        print(f"\n=== row {i}: harness={row['harness']}  "
              f"with_skills={row.get('with_skills', False)}  "
              f"model={row.get('model', 'default')} ===")
        print(f"  run_id:   {run_id}")
        print(f"  grade_id: {grade_id}")

        row_failed = False
        if "rollout" in stages and not row_failed:
            rc = run_stage("rollout", build_rollout_cmd(row, cfg, run_id), args.dry_run)
            if rc != 0:
                print(f"  [rollout] FAILED rc={rc}")
                row_failed = True
        if "judge" in stages and not row_failed:
            rc = run_stage("judge", build_judge_cmd(row, cfg, run_id, grade_id), args.dry_run)
            if rc != 0:
                print(f"  [judge] FAILED rc={rc}")
                row_failed = True
        if "aggregate" in stages and not row_failed:
            rc = run_stage("aggregate", build_aggregate_cmd(row, cfg, grade_id), args.dry_run)
            if rc != 0:
                print(f"  [aggregate] FAILED rc={rc}")
                row_failed = True

        if row_failed:
            failed += 1
            if not args.keep_going:
                print(f"\nABORT (use --keep-going to continue past row failures)")
                return 1

    executed = sum(1 for i in range(len(matrix)) if row_filter is None or i in row_filter)
    print(f"\ndone. {executed} rows executed (of {len(matrix)} total), {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
