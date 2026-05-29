#!/usr/bin/env python3
"""Aggregator: invoke analyze_grades.py over a grades/<grade_id>/ directory.

Thin shim — analyze_grades.py already handles the cascade scoring and the
grades_dir/<task_id>/judge_phase_<phase>.json layout we produce.

The analyzer lives in this package (vendored from agent_skill_helps_training/
test/analyze_grades.py) so skillsbench stays self-contained — moving or
renaming the parent dir no longer breaks aggregation.  Re-sync from upstream
by re-copying the file when the cascade-scoring logic changes; the CLI
contract (positional grades_dir + --json/--md/--per-task/--per-task-sort) is
the seam that must remain stable.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# In-tree copy of analyze_grades.py — same directory as this shim.  Override
# with `--analyze-script` if you want to point at a different (e.g. patched)
# version while iterating.
DEFAULT_ANALYZE = Path(__file__).resolve().parent / "analyze_grades.py"


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate phase judges into per-task scores")
    parser.add_argument("--grades", required=True, help="grades/<grade_id>/")
    parser.add_argument("--analyze-script", default=str(DEFAULT_ANALYZE))
    parser.add_argument("--json", default=None)
    parser.add_argument("--md", default=None)
    parser.add_argument("--per-task", action="store_true")
    parser.add_argument("--per-task-sort", choices=["task", "norm"], default=None)
    args = parser.parse_args()

    analyze = Path(args.analyze_script)
    if not analyze.exists():
        sys.exit(f"analyze_grades.py not found: {analyze}")

    cmd = [sys.executable, str(analyze), str(Path(args.grades).resolve())]
    if args.json:
        cmd += ["--json", args.json]
    if args.md:
        cmd += ["--md", args.md]
    if args.per_task:
        cmd += ["--per-task"]
    if args.per_task_sort:
        cmd += ["--per-task-sort", args.per_task_sort]

    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
