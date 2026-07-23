#!/usr/bin/env python3
"""Adapter: search_env_search_query dataset → Harbor/BenchFlow tasks/ (rollout-only).

Input layout (one dir per item, as shipped in the HF tarballs):
    <src>/<item>/
        env/
            .claude/skills/<item>/{SKILL.md, ...}   # exactly one skill, named == item
            <working files…>  extracted/<repo…>  req-*.zip
        query/query.md                               # the user request

Output layout (what BenchFlow + skillsbench_x rollout expects), minus rubric/ —
this batch is rollout-only (no LLM-as-judge, no rubrics):
    <output-root>/<run-id>/<item>/
        task.toml
        instruction.md                  # = query/query.md verbatim
        environment/
            Dockerfile                  # FROM skillsbench-base + COPY files + build-time setup.sh
            setup.sh                    # no-op default; per-task BUILD-TIME setup (you fill it in)
            files/<env files>           # env/* minus .claude (and minus redundant req-*.zip)
            skills/<item>/{SKILL.md…}   # env/.claude/skills/<item>/
        tests/test.sh                   # no-op reward=1 (TaskPaths.is_valid requires test.sh)

The prompt that goes *before* the query is NOT baked here — it is injected at
rollout time (rollout.py --prompt-file → bench run --prompt-prefix), so the same
materialized task serves any prompt and prompt changes need no re-materialize.

Per-task docker is configured *after* materialization by editing each task's
environment/ (setup.sh for build-time setup; or task.toml docker_image for a
prebuilt image). Re-running this script is safe: by default it preserves an
existing Dockerfile / setup.sh / task.toml and only refreshes the data
(instruction.md, files/, skills/, tests/). Pass --force to rewrite everything.

Typical usage:
    python3 skillsbench_x/from_search_env.py \\
        --src /l/users/zhengxi.li/dataset/search_env_search_query/jun_16_syn_50_data \\
        --names-file experiments/configs/search-env/names-jun50.txt \\
        --src /l/users/zhengxi.li/dataset/search_env_search_query/jun_16_syn_100_data \\
        --names-file experiments/configs/search-env/names-jun100.txt \\
        --output-root /l/users/zhengxi.li/tasks_runtime \\
        --run-id search-env
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

DOCKERFILE = """\
FROM skillsbench-base:latest

COPY files /app/
WORKDIR /app

# Per-task BUILD-TIME setup. Runs during `docker build` (root, cwd=/app).
# Edit environment/setup.sh; leave it as the no-op default if the task needs nothing.
COPY setup.sh /tmp/setup.sh
RUN bash /tmp/setup.sh
"""

SETUP_SH = """\
#!/bin/bash
set -euo pipefail
# Per-task BUILD-TIME setup — runs during `docker build` (root, cwd=/app, network up).
# /app already contains this task's env files (COPY files /app/).
#
# Put install / unzip / compile / file-generation steps here. Notes:
#   * Runtime may be OFFLINE (task.toml allow_internet=false) — install EVERYTHING the
#     agent needs at runtime HERE, at build time.
#   * Do NOT start long-running services (build layers don't keep processes).
#   * Base image already has Node, Python3, pytest, codex-acp — don't reinstall those.
#   * Python:  pip install --break-system-packages <pkg>    |    Node:  npm install
#   * Must be skill-agnostic: no-skill and with-skill runs share this same image,
#     and /app has NO skill files at build time.
#
# (no-op by default — fill in as needed)
"""

TASK_TOML = """\
version = "1.0"

[metadata]
task_id = "{task_id}"
category = "search-env"
source = "search-env:{source_root}"

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
# Per-task docker override: to use a fully prebuilt image instead of building the
# Dockerfile, uncomment and set the line below. The image MUST contain the agent
# (derive it from skillsbench-base, or a slow runtime agent-install will be attempted).
# docker_image = "your-image:tag"
"""

TEST_SH = """\
#!/bin/bash
# No-op verifier: this batch is rollout-only (no rubric / graded externally).
mkdir -p /logs/verifier
echo 1 > /logs/verifier/reward.txt
exit 0
"""


def discover_items(src: Path) -> dict[str, Path]:
    """Map item-name -> item-dir for every valid item directly under src."""
    items: dict[str, Path] = {}
    for entry in sorted(src.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "query" / "query.md").is_file():
            continue
        if not (entry / "env").is_dir():
            continue
        items[entry.name] = entry
    return items


def load_names(paths: list[Path], inline: str | None) -> set[str]:
    """Union of names from --names-file(s) and --only; empty set means 'all'."""
    names: set[str] = set()
    for p in paths:
        if not p.exists():
            sys.exit(f"--names-file not found: {p}")
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            names.add(line)
    if inline:
        names.update(n.strip() for n in inline.split(",") if n.strip())
    return names


def zips_to_skip(item_dir: Path, env_dir: Path, keep_zips: bool) -> set[str]:
    """Provably-redundant top-level req-*.zip to omit from files/.

    A req-*.zip is skipped ONLY when both hold (so no task-relevant data is lost):
      * query.md does not reference the zip by name (otherwise the agent wants the
        archive itself), and
      * every top-level entry the zip would unpack to already exists under
        env/extracted/ — i.e. the archive's content is genuinely present unpacked.

    This is deliberately conservative: a zip whose content is NOT in extracted/
    (e.g. team-designer-1's `openclaw` archives, which never got extracted) is
    kept. Non-`req-*.zip` archives are always kept. Source dataset is untouched.
    """
    if keep_zips:
        return set()
    extracted = env_dir / "extracted"
    if not extracted.is_dir():
        return set()
    query = (item_dir / "query" / "query.md").read_text(errors="ignore")
    present = {p.name for p in extracted.iterdir()}
    skip: set[str] = set()
    for z in env_dir.glob("req-*.zip"):
        if z.name in query:
            continue
        try:
            with zipfile.ZipFile(z) as zf:
                tops = {n.split("/", 1)[0] for n in zf.namelist() if n.strip("/")}
        except (zipfile.BadZipFile, OSError):
            continue  # unreadable → keep it
        if tops and tops <= present:
            skip.add(z.name)
    return skip


def copy_env_files(env_dir: Path, dst_files: Path, skip_names: set[str]) -> None:
    """Copy env/* into dst_files/, excluding .claude and any skipped zips."""
    dst_files.mkdir(parents=True, exist_ok=True)
    for entry in sorted(env_dir.iterdir()):
        if entry.name == ".claude" or entry.name in skip_names:
            continue
        if entry.is_dir():
            shutil.copytree(entry, dst_files / entry.name)
        else:
            shutil.copy2(entry, dst_files / entry.name)


def copy_skill_content(env_dir: Path, dst: Path) -> int:
    """Copy env/.claude/skills/<skill>/ into dst/<skill>/. Returns #skills copied."""
    dst.mkdir(parents=True, exist_ok=True)
    inner = env_dir / ".claude" / "skills"
    if not inner.is_dir():
        return 0
    n = 0
    for skill_dir in sorted(inner.iterdir()):
        if skill_dir.is_dir():
            shutil.copytree(skill_dir, dst / skill_dir.name)
            n += 1
    return n


def write_task(name: str, source_root: str, item_dir: Path, run_dir: Path,
               force: bool, keep_zips: bool) -> dict:
    """Materialize one task dir. Returns a small report dict."""
    task_dir = run_dir / name
    env_src = item_dir / "env"
    task_dir.mkdir(parents=True, exist_ok=True)

    # Always-refreshed data (cheap to regenerate, never hand-edited):
    (task_dir / "instruction.md").write_text((item_dir / "query" / "query.md").read_text())

    env_dir = task_dir / "environment"
    env_dir.mkdir(exist_ok=True)

    files_dir = env_dir / "files"
    if files_dir.exists():
        shutil.rmtree(files_dir)
    skip = zips_to_skip(item_dir, env_src, keep_zips)
    copy_env_files(env_src, files_dir, skip)

    skills_dir = env_dir / "skills"
    if skills_dir.exists():
        shutil.rmtree(skills_dir)
    n_skills = copy_skill_content(env_src, skills_dir)

    tests = task_dir / "tests"
    tests.mkdir(exist_ok=True)
    test_sh = tests / "test.sh"
    test_sh.write_text(TEST_SH)
    test_sh.chmod(0o755)

    # Hand-editable docker config — only (re)written when absent or --force:
    wrote_cfg = []
    df = env_dir / "Dockerfile"
    if force or not df.exists():
        df.write_text(DOCKERFILE)
        wrote_cfg.append("Dockerfile")
    su = env_dir / "setup.sh"
    if force or not su.exists():
        su.write_text(SETUP_SH)
        su.chmod(0o755)
        wrote_cfg.append("setup.sh")
    tt = task_dir / "task.toml"
    if force or not tt.exists():
        tt.write_text(TASK_TOML.format(task_id=name, source_root=source_root))
        wrote_cfg.append("task.toml")

    # Light leak check: item/skill name appearing in the agent-visible query.
    leak = name in (task_dir / "instruction.md").read_text(errors="ignore")

    return {
        "name": name,
        "source": source_root,
        "skipped_zips": sorted(skip),
        "n_skills": n_skills,
        "wrote_config": wrote_cfg,
        "query_name_leak": leak,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="search_env dataset → BenchFlow tasks/ (rollout-only)")
    ap.add_argument("--src", action="append", required=True,
                    help="dataset dir containing <item>/ subdirs (repeatable)")
    ap.add_argument("--output-root", required=True,
                    help="tasks land at <output-root>/<run-id>/<item>/")
    ap.add_argument("--run-id", default=None,
                    help="subdir under --output-root (default: UTC date stamp)")
    ap.add_argument("--names-file", action="append", default=[],
                    help="file with one item name per line to include (repeatable; "
                         "'#' comments ok). Union across files. Omit = all items.")
    ap.add_argument("--only", default=None, help="comma-separated item names to include")
    ap.add_argument("--limit", type=int, default=None, help="cap number of items (after filtering)")
    ap.add_argument("--keep-zips", action="store_true",
                    help="copy redundant req-*.zip too (default: skip when an extracted/ copy "
                         "exists and query.md doesn't reference the zip)")
    ap.add_argument("--force", action="store_true",
                    help="rewrite Dockerfile/setup.sh/task.toml even if they already exist")
    ap.add_argument("--dry-run", action="store_true", help="list what would be written, don't copy")
    args = ap.parse_args()

    srcs = [Path(s).resolve() for s in args.src]
    for s in srcs:
        if not s.is_dir():
            sys.exit(f"--src not a directory: {s}")

    # Gather all items across srcs; hard-error on cross-src name collisions.
    name_to_src: dict[str, tuple[str, Path]] = {}
    for s in srcs:
        for name, item_dir in discover_items(s).items():
            if name in name_to_src:
                sys.exit(f"duplicate item name {name!r} in {name_to_src[name][0]} and {s.name}")
            name_to_src[name] = (s.name, item_dir)

    wanted = load_names([Path(p) for p in args.names_file], args.only)
    if wanted:
        missing = sorted(wanted - set(name_to_src))
        if missing:
            sys.exit("requested names not found in any --src:\n" + "\n".join(f"  - {m}" for m in missing))
        selected = sorted(wanted)
    else:
        selected = sorted(name_to_src)
    if args.limit:
        selected = selected[: args.limit]
    if not selected:
        sys.exit("no items selected")

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_root).resolve() / run_id

    print(f"srcs:    {', '.join(s.name for s in srcs)}")
    print(f"output:  {run_dir}")
    print(f"selected: {len(selected)} item(s)")
    if args.dry_run:
        for name in selected:
            print(f"  [dry-run] {name}  (from {name_to_src[name][0]})")
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)

    reports = []
    for name in selected:
        source_root, item_dir = name_to_src[name]
        rep = write_task(name, source_root, item_dir, run_dir, args.force, args.keep_zips)
        reports.append(rep)
        zinfo = f"  (skipped {len(rep['skipped_zips'])} redundant zip)" if rep["skipped_zips"] else ""
        leak = "  LEAK!" if rep["query_name_leak"] else ""
        print(f"  [{source_root:>18}] {name}{zinfo}{leak}")

    # Selection record + manifest next to the tasks.
    manifest = {
        "run_id": run_id,
        "srcs": [str(s) for s in srcs],
        "n_tasks": len(reports),
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tasks": reports,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    n_zip = sum(len(r["skipped_zips"]) for r in reports)
    leaks = [r["name"] for r in reports if r["query_name_leak"]]
    print(f"\nwrote {len(reports)} task(s) → {run_dir}")
    print(f"redundant req-*.zip skipped: {n_zip} (kept all others; source dataset untouched)")
    if leaks:
        print(f"NOTE: item name appears in query.md for {len(leaks)} task(s) "
              f"(skill-name visible to agent in no-skill runs): {', '.join(leaks)}")
    print(f"manifest: {run_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
