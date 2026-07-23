#!/usr/bin/env python3
"""Adapter: flat-format deliverable (HuggingFace artifact) → Harbor tasks/ —
"hard" group variant.

Same input/output layout and packaging logic as ``from_flat.py``; the ONLY
difference is the skill_name -> id mapping rule:

  ``from_flat.py``   uses a fixed CANONICAL_SKILL_MAPPING (group1 1..89,
                     group2 90..181) and errors on any skill not in it.
  ``from_flat_hard.py`` (this file) ALLOCATES ids on the fly, sequentially,
                     starting at ``--start-id`` (default 182) over the
                     discovered skills in sorted order.  For the 334-skill
                     ``skill_use_eval_hard`` deliverable this yields the id
                     range [182, 182+333] = [182, 515].

Input layout (what's on HuggingFace):
    <flat>/
        run_env/<skill_name>/
            .claude/skills/<skill_name>/{SKILL.md, ...}
            <env file 1> ...
        user_query/<skill_name>/revised_user_need.md
        rubrics/<skill_name>/{rubric.json, judge_phase_*.md}

Output layout (what BenchFlow + skillsbench_x rollout expects):
    <output>/<run_id>/<task_id>/
        task.toml
        instruction.md
        environment/Dockerfile
        environment/files/<env files at agent workdir root>
        environment/skills/<skill_name>/{SKILL.md, ...}
        rubric/{rubric.json, judge_phase_*.md}
        tests/test.sh

Typical usage:
    python3 skillsbench_x/from_flat_hard.py \\
        --flat-dir skill_use_eval_hard \\
        --output-root tasks_runtime \\
        --run-id group_hard_300
    # → tasks_runtime/group_hard_300/{182,183,...,515}/
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

RUBRIC_FILES = (
    "rubric.json",
    "judge_phase_skill_identification.md",
    "judge_phase_module_sequence.md",
    "judge_phase_post_processing.md",
)


TASK_TOML = """\
version = "1.0"

[metadata]
task_id = "{task_id}"
hidden_skill_name = "{skill_name}"
category = "skill-eval"
source = "flat:{source_root}"

[verifier]
timeout_sec = 60

[agent]
timeout_sec = 1800

[environment]
build_timeout_sec = 600.0
cpus = 2
memory_mb = 4096
storage_mb = 10240
gpus = 0
allow_internet = false
"""

DOCKERFILE = """\
FROM skillsbench-base:latest

COPY files /app/

WORKDIR /app
"""

TEST_SH = """\
#!/bin/bash
# No-op verifier: real scoring is done by an external LLM-as-judge.
mkdir -p /logs/verifier
echo 1 > /logs/verifier/reward.txt
exit 0
"""


def discover_skills(flat_dir: Path) -> list[str]:
    run_env = flat_dir / "run_env"
    query = flat_dir / "user_query"
    rubric = flat_dir / "rubrics"
    if not (run_env.is_dir() and query.is_dir() and rubric.is_dir()):
        sys.exit(
            f"--flat-dir {flat_dir} does not contain run_env/, user_query/, rubrics/."
        )
    skills = []
    for entry in sorted(run_env.iterdir()):
        if not entry.is_dir():
            continue
        if not (query / entry.name / "revised_user_need.md").is_file():
            continue
        if not all((rubric / entry.name / f).is_file() for f in RUBRIC_FILES):
            continue
        skills.append(entry.name)
    return skills


def copy_env_files(src_run_env_skill: Path, dst_files: Path) -> None:
    """Copy run_env/<skill>/* into dst_files/, excluding the .claude/ tree."""
    dst_files.mkdir(parents=True, exist_ok=True)
    for entry in src_run_env_skill.iterdir():
        if entry.name == ".claude":
            continue
        if entry.is_dir():
            shutil.copytree(entry, dst_files / entry.name)
        else:
            shutil.copy2(entry, dst_files / entry.name)


def copy_skill_content(src_skill_root: Path, dst: Path) -> None:
    """Copy the inner skill directory (.claude/skills/<skill>/) to dst/<skill>/."""
    dst.mkdir(parents=True, exist_ok=True)
    inner = src_skill_root / ".claude" / "skills"
    if not inner.is_dir():
        return
    for skill_dir in inner.iterdir():
        if skill_dir.is_dir():
            shutil.copytree(skill_dir, dst / skill_dir.name)


def write_task(skill_name: str, task_id: int, flat_dir: Path, run_dir: Path) -> list[str]:
    """Write one tasks/<task_id>/ tree and return a list of leak-check hits."""
    task_dir = run_dir / str(task_id)
    if task_dir.exists():
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True)

    src_run_env = flat_dir / "run_env" / skill_name
    src_query = flat_dir / "user_query" / skill_name / "revised_user_need.md"
    src_rubric = flat_dir / "rubrics" / skill_name

    shutil.copy2(src_query, task_dir / "instruction.md")

    env_dir = task_dir / "environment"
    env_dir.mkdir()
    (env_dir / "Dockerfile").write_text(DOCKERFILE)
    copy_env_files(src_run_env, env_dir / "files")
    copy_skill_content(src_run_env, env_dir / "skills")

    rubric_out = task_dir / "rubric"
    rubric_out.mkdir()
    for fname in RUBRIC_FILES:
        shutil.copy2(src_rubric / fname, rubric_out / fname)

    tests = task_dir / "tests"
    tests.mkdir()
    test_sh = tests / "test.sh"
    test_sh.write_text(TEST_SH)
    test_sh.chmod(0o755)

    (task_dir / "task.toml").write_text(
        TASK_TOML.format(
            task_id=task_id,
            skill_name=skill_name,
            source_root=flat_dir.name,
        )
    )

    # leak check: skill name in agent-visible files?
    hits: list[str] = []
    needle = skill_name.encode("utf-8")
    targets = [task_dir / "instruction.md", *list((env_dir / "files").rglob("*"))]
    for t in targets:
        if not t.is_file():
            continue
        try:
            with t.open("rb") as f:
                if needle in f.read():
                    hits.append(str(t.relative_to(run_dir)))
        except OSError:
            continue
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(
        description="flat-format deliverable → Harbor tasks/ (hard group; sequential id allocation)"
    )
    parser.add_argument("--flat-dir", required=True,
                        help="Path to a flat-layout dir (contains run_env/, user_query/, rubrics/)")
    parser.add_argument("--output-root", required=True,
                        help="Output root; final tasks land at <output-root>/<run-id>/<task_id>/")
    parser.add_argument("--run-id", default="group_hard_300",
                        help="Subdir name under --output-root (default: group_hard_300)")
    parser.add_argument("--start-id", type=int, default=182,
                        help="First task id; ids are allocated sequentially over the "
                             "discovered skills in sorted order (default: 182)")
    parser.add_argument("--only", default=None,
                        help="Comma-separated skill names to package (default: all discovered)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--strict-leak", action="store_true",
                        help="Exit non-zero if any task leaks its skill_name into agent-visible files")
    args = parser.parse_args()

    flat_dir = Path(args.flat_dir).resolve()

    skills = discover_skills(flat_dir)
    if args.only:
        keep = set(args.only.split(","))
        skills = [s for s in skills if s in keep]
    if args.limit:
        skills = skills[: args.limit]
    if not skills:
        sys.exit(f"no packageable skills under {flat_dir}")

    # Mapping rule (rewritten vs from_flat.py): sequential ids from --start-id
    # over the discovered skills in sorted order.  `skills` is already sorted by
    # discover_skills.
    final_map = {name: args.start_id + i for i, name in enumerate(skills)}
    id_lo = args.start_id
    id_hi = args.start_id + len(skills) - 1
    print(f"allocating {len(skills)} ids in range [{id_lo}, {id_hi}]")

    run_id = args.run_id
    run_dir = Path(args.output_root).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"output: {run_dir}")

    leaks_report: list[tuple[str, int, list[str]]] = []
    for skill_name in skills:
        tid = final_map[skill_name]
        hits = write_task(skill_name, tid, flat_dir, run_dir)
        if hits:
            leaks_report.append((skill_name, tid, hits))
        print(f"  [{tid:>3}] {skill_name}" + ("  LEAK!" if hits else ""))

    map_path = run_dir / "tasks_skill_map.tsv"
    with map_path.open("w") as f:
        f.write("task_id\tskill_name\n")
        for s in sorted(skills, key=lambda n: final_map[n]):
            f.write(f"{final_map[s]}\t{s}\n")
    print(f"\nwrote skill-id map: {map_path}")

    manifest = {
        "run_id": run_id,
        "source_flat_dir": str(flat_dir),
        "id_range": [id_lo, id_hi],
        "start_id": args.start_id,
        "n_packaged_skills": len(skills),
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    if leaks_report:
        print("\nLEAK WARNING — skill name found inside agent-visible files:")
        for skill_name, tid, files in leaks_report:
            for rel in files:
                print(f"  task {tid} ({skill_name}): {rel}")
        if args.strict_leak:
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
