#!/usr/bin/env python3
"""Adapter: flat-format deliverable (HuggingFace artifact) → Harbor tasks/.

Input layout (what's on HuggingFace):
    <flat>/
        run_env/<skill_name>/
            .claude/skills/<skill_name>/{SKILL.md, ...}
            <env file 1>
            <env file 2>
            ...
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

Task IDs come from the two hardcoded mapping files:
    test/group1/no_skills/latest_read_only/rename_skill_dirs_to_ids.py  (IDs 1..89)
    test/group2/no_skills/lave_cc_sn_hg/rename_skill_dirs_to_ids.py     (IDs 90..181)

Rules:
- A skill listed in the merged mapping but absent from the HF download is
  silently skipped (the mapping is the canonical ID registry; not every group
  is always present in every artifact).
- A skill present in the HF download but NOT in the merged mapping is a hard
  error — IDs must come from the canonical registry, no auto-allocation.

Typical server-side usage:
    git clone <hf-repo>  # → ./latest_deliverable/{run_env, user_query, rubrics}
    python3 skillsbench_x/from_flat.py \\
        --flat-dir ./latest_deliverable \\
        --output-root tasks_runtime \\
        --run-id 20260601-group1
    # → tasks_runtime/20260601-group1/{1,2,5,7,...}/
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT.parents[1]
DEFAULT_SKILL_MAPS = (
    PROJECT_ROOT / "test" / "group1" / "no_skills" / "latest_read_only"
    / "rename_skill_dirs_to_ids.py",
    PROJECT_ROOT / "test" / "group2" / "no_skills" / "lave_cc_sn_hg"
    / "rename_skill_dirs_to_ids.py",
)

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


def load_skill_mapping(path: Path) -> dict[str, int]:
    text = path.read_text()
    body = re.search(r"SKILL_MAPPING\s*=\s*\[(.*?)\]", text, re.S)
    if not body:
        raise RuntimeError(f"could not find SKILL_MAPPING in {path}")
    mapping: dict[str, int] = {}
    for line in body.group(1).splitlines():
        m = re.match(r'\s*\("([^"]+)",\s*(\d+)\)', line)
        if m:
            mapping[m.group(1)] = int(m.group(2))
    if not mapping:
        raise RuntimeError(f"SKILL_MAPPING parsed empty from {path}")
    return mapping


def load_skill_map_tsv(path: Path) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("task_id"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            try:
                tid = int(parts[0])
            except ValueError:
                continue
            mapping[parts[1]] = tid
    return mapping


def merge_skill_mappings(paths: list[Path]) -> dict[str, int]:
    """Load all mapping sources and merge.  Raises on cross-source conflicts."""
    merged: dict[str, int] = {}
    id_to_name: dict[int, str] = {}
    name_origin: dict[str, Path] = {}
    for path in paths:
        if not path.exists():
            raise SystemExit(f"--skill-map source not found: {path}")
        if path.suffix == ".py":
            m = load_skill_mapping(path)
        else:
            m = load_skill_map_tsv(path)
        for name, tid in m.items():
            if name in merged and merged[name] != tid:
                raise SystemExit(
                    f"skill-mapping conflict: '{name}' has id {merged[name]} in "
                    f"{name_origin[name]} but id {tid} in {path}"
                )
            if tid in id_to_name and id_to_name[tid] != name:
                raise SystemExit(
                    f"skill-mapping conflict: id {tid} is used by both "
                    f"'{id_to_name[tid]}' and '{name}' (latest source: {path})"
                )
            merged[name] = tid
            id_to_name[tid] = name
            name_origin.setdefault(name, path)
    return merged


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
    parser = argparse.ArgumentParser(description="flat-format deliverable → Harbor tasks/")
    parser.add_argument("--flat-dir", required=True,
                        help="Path to a flat-layout dir (contains run_env/, user_query/, rubrics/)")
    parser.add_argument("--output-root", required=True,
                        help="Output root; final tasks land at <output-root>/<run-id>/<task_id>/")
    parser.add_argument("--run-id", default=None,
                        help="Subdir name under --output-root; defaults to UTC date stamp")
    parser.add_argument("--skill-map", action="append", default=None,
                        help="Path to a skill-mapping source. Accepts either a python "
                             "rename_skill_dirs_to_ids.py file or a TSV of "
                             "task_id<TAB>skill_name.  Pass multiple times to merge "
                             "sources.  Defaults to the two hardcoded group1+group2 "
                             "rename_skill_dirs_to_ids.py files.")
    parser.add_argument("--only", default=None,
                        help="Comma-separated skill names to package (default: all discovered)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--strict-leak", action="store_true",
                        help="Exit non-zero if any task leaks its skill_name into agent-visible files")
    args = parser.parse_args()

    flat_dir = Path(args.flat_dir).resolve()
    skill_map_paths = [Path(p).resolve() for p in (args.skill_map or [str(p) for p in DEFAULT_SKILL_MAPS])]

    base_map = merge_skill_mappings(skill_map_paths)
    print(f"loaded merged mapping: {len(base_map)} skills from {len(skill_map_paths)} source(s)")

    skills = discover_skills(flat_dir)
    if args.only:
        keep = set(args.only.split(","))
        skills = [s for s in skills if s in keep]
    if args.limit:
        skills = skills[: args.limit]
    if not skills:
        sys.exit(f"no packageable skills under {flat_dir}")

    # Hard constraint: every discovered skill must be in the canonical mapping.
    # A skill present in HF but missing from mapping aborts the whole run so
    # IDs are never silently invented.  The reverse direction (mapping entries
    # with no HF data) is fine — we just skip them.
    unmapped = sorted(s for s in skills if s not in base_map)
    if unmapped:
        msg = [
            f"{len(unmapped)} skill(s) in --flat-dir are not present in the canonical mapping:",
            *[f"  - {s}" for s in unmapped],
            "",
            "Add them to one of the mapping sources before re-running.  Sources used:",
            *[f"  - {p}" for p in skill_map_paths],
        ]
        sys.exit("\n".join(msg))
    final_map = base_map

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_root).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"output: {run_dir}")
    print(f"adapting {len(skills)} skill(s) out of {len(base_map)} canonical mapping entries")
    skipped = sorted(set(base_map) - set(skills))
    if skipped:
        print(f"(mapping entries with no HF data, silently skipped: {len(skipped)})")

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
        "skill_map_sources": [str(p) for p in skill_map_paths],
        "n_canonical_mapping_entries": len(base_map),
        "n_packaged_skills": len(skills),
        "n_canonical_skipped_no_hf_data": len(skipped),
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
