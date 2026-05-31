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

Task IDs come from CANONICAL_SKILL_MAPPING embedded below — the canonical
skill_name -> integer-id registry for group1 (IDs 1..89) and group2 (IDs
90..181).  This module is self-contained: by default it no longer reads the
external test/group{1,2}/.../rename_skill_dirs_to_ids.py files.  (Pass
--skill-map one or more times to override with external .py or TSV sources.)

Rules:
- A skill listed in the canonical mapping but absent from the HF download is
  silently skipped (the mapping is the canonical ID registry; not every group
  is always present in every artifact).
- A skill present in the HF download but NOT in the canonical mapping is a hard
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

# Canonical skill_name -> integer-id registry, embedded so this module is
# self-contained (no dependency on ../test/group{1,2}/.../rename_skill_dirs_to_ids.py).
# Mirrors those two files verbatim:
#   group1 = test/group1/no_skills/latest_read_only/rename_skill_dirs_to_ids.py (IDs 1..89)
#   group2 = test/group2/no_skills/lave_cc_sn_hg/rename_skill_dirs_to_ids.py    (IDs 90..181)
# IDs are stable and contiguous 1..181, no gaps, no duplicates.  Edit here if the
# canonical registry changes (or pass --skill-map to override from external sources).
CANONICAL_SKILL_MAPPING: list[tuple[str, int]] = [
    # ── group1 (latest_read_only): IDs 1..89 ──────────────────────────────────
    ("BMAD-Method", 1),
    ("Directorofoperations", 2),
    ("Jira-Orchestration-Workflow", 3),
    ("Plantcapability", 4),
    ("Startup-CTO", 5),
    ("abridge-upgrade-migration", 6),
    ("accelint-react-testing", 7),
    ("analytics-tracking-5", 8),
    ("arc42-documentation", 9),
    ("assess-gdpr", 10),
    ("axiom-background-processing-diag-1", 11),
    ("axiom-build-debugging-1", 12),
    ("axiom-ios-performance", 13),
    ("axiom-keychain-diag-1", 14),
    ("axiom-storage-diag-1", 15),
    ("axiom-swiftui-nav-1", 16),
    ("canva-known-pitfalls", 17),
    ("ce_compound", 18),
    ("chro-advisor-2", 19),
    ("churn-prevention-3", 20),
    ("clickhouse-architect", 21),
    ("cloudflare-knowledge", 22),
    ("content-reflection", 23),
    ("create-viral-content-1", 24),
    ("crystal-concurrency", 25),
    ("database-patterns-1", 26),
    ("design-system-starter-1", 27),
    ("develop-2", 28),
    ("devops-deployer", 29),
    ("devops-excellence", 30),
    ("dual-write", 31),
    ("facebook-ads-1", 32),
    ("final-cut-pro", 33),
    ("fix-bug", 34),
    ("founder-coach-1", 35),
    ("fuzzing-obstacles", 36),
    ("gcse-chemistry-tutor", 37),
    ("go-web-expert", 38),
    ("godot-genre-moba", 39),
    ("godot-save-load-systems", 40),
    ("golang-structs-interfaces", 41),
    ("gsd-verifier", 42),
    ("gtm-enterprise-account-planning", 43),
    ("harmonyos-app", 44),
    ("incident-runbook-templates-7", 45),
    ("instrumentation-planning", 46),
    ("internal-narrative-2", 47),
    ("issue-review", 48),
    ("key-management-orchestrator", 49),
    ("langchain-common-errors", 50),
    ("lindy-prod-checklist", 51),
    ("livestream-sales", 52),
    ("machine-learning-ops-ml-pipeline-2", 53),
    ("marketing-demand-acquisition", 54),
    ("material-selection", 55),
    ("metadata-optimization", 56),
    ("model-routing", 57),
    ("multi-platform-launch", 58),
    ("nushell-pro", 59),
    ("nushell-usage", 60),
    ("patent-application-creator", 61),
    ("pip-documentation", 62),
    ("private-domain", 63),
    ("pulumi-best-practices", 64),
    ("rca-analysis", 65),
    ("reputation-recovery", 66),
    ("runbooks-incident-response", 67),
    ("rust-ownership-system", 68),
    ("sadd_launch-sub-agent", 69),
    ("sales-methodology-implementer", 70),
    ("sales-narrative", 71),
    ("salesforce-development-2", 72),
    ("salesforce-known-pitfalls", 73),
    ("sap-hana-cloud-data-intelligence", 74),
    ("security-auth", 75),
    ("security-bun", 76),
    ("startup-pivoting", 77),
    ("supabase-troubleshooting", 78),
    ("system-architecture", 79),
    ("system-design-10", 80),
    ("system-table-change", 81),
    ("tagline-creation-strategies", 82),
    ("tailwindcss-accessibility", 83),
    ("testing-strategies-1", 84),
    ("travel-health-analyzer", 85),
    ("travel-planner-4", 86),
    ("tui-input", 87),
    ("understanding-tauri-ecosystem-security", 88),
    ("windows-remote-desktop-connection-doctor", 89),
    # ── group2 (lave_cc_sn_hg): IDs 90..181 ───────────────────────────────────
    ("Cncsetup", 90),
    ("account-deletion", 91),
    ("acquisition-channel-advisor", 92),
    ("ad-creative-2", 93),
    ("agent-teams-simplify-and-harden-1", 94),
    ("app-onboarding", 95),
    ("architecting-data", 96),
    ("aws-cloud-2", 97),
    ("axiom-background-processing-ref-1", 98),
    ("axiom-ios-testing", 99),
    ("axiom-storage-management-ref-1", 100),
    ("axiom-storekit-ref-1", 101),
    ("axiom-uikit-bridging-1", 102),
    ("azure-cloud-2", 103),
    ("behavioral-product-design", 104),
    ("building-malware-incident-communication-template", 105),
    ("change-management-4", 106),
    ("chrome-extension-developer", 107),
    ("cicd-pipeline-qe-orchestrator", 108),
    ("clinpgx-database-4", 109),
    ("competitive-intelligence-market-research", 110),
    ("compliance-architecture", 111),
    ("conducting-user-interviews", 112),
    ("content-review", 113),
    ("content-writing-thought-leadership", 114),
    ("cosmosdb-best-practices", 115),
    ("cosmosdb-datamodeling", 116),
    ("create-meta-prompts", 117),
    ("data-export", 118),
    ("database-design-patterns", 119),
    ("deploying-on-azure", 120),
    ("django-security-1", 121),
    ("ecs-deployment", 122),
    ("eks-security", 123),
    ("ena-database", 124),
    ("error-diagnostics-smart-debug", 125),
    ("folder-structure-blueprint-generator", 126),
    ("full-stack-doc", 127),
    ("fullstack-dev", 128),
    ("gcse-art-tutor", 129),
    ("gemini-tools", 130),
    ("godot-characterbody-2d", 131),
    ("godot-debugging-profiling", 132),
    ("golang-concurrency-patterns", 133),
    ("golang-dependency-management", 134),
    ("graph-schema", 135),
    ("gtm-board-and-investor-communication", 136),
    ("gtm-operating-cadence", 137),
    ("gtm-technical-product-pricing", 138),
    ("hk-stock-analysis", 139),
    ("iam-identity-management", 140),
    ("incident-responder", 141),
    ("ios-animation-design", 142),
    ("iso-implementation-guide", 143),
    ("landing-page-design", 144),
    ("langgraph-code-review", 145),
    ("legal-response", 146),
    ("lindy-data-handling", 147),
    ("liquid-theme-a11y", 148),
    ("mapbox-token-security", 149),
    ("marketing-mode", 150),
    ("memory-intake", 151),
    ("mentoring-juniors", 152),
    ("moai-platform-chrome-extension", 153),
    ("mobile-platform-specialist", 154),
    ("network-engineer-3", 155),
    ("networking-management", 156),
    ("observability-monitor", 157),
    ("opentargets-database", 158),
    ("optimizing-sql", 159),
    ("output-sanitizer", 160),
    ("penalty-avoidance", 161),
    ("persistence-setup", 162),
    ("postgres-ops", 163),
    ("qbr-facilitator", 164),
    ("resilience-patterns", 165),
    ("rn-performance", 166),
    ("salesloft-prod-checklist", 167),
    ("sf-apex", 168),
    ("sop-api-development", 169),
    ("spawn", 170),
    ("sql-cookbook", 171),
    ("stock-question-refiner", 172),
    ("strategic-alignment-2", 173),
    ("suitability-report-generator-1", 174),
    ("support-operations", 175),
    ("system-createcli", 176),
    ("tooluniverse-protein-structure-prediction", 177),
    ("transcription-to-content", 178),
    ("tri-ai-collaboration", 179),
    ("v4-security-foundations", 180),
    ("write-prd", 181),
]

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


def canonical_mapping_dict() -> dict[str, int]:
    """Build the embedded canonical skill_name -> id mapping from
    CANONICAL_SKILL_MAPPING, validating against duplicate names or ids so a bad
    manual edit fails loudly instead of silently shadowing an entry."""
    mapping: dict[str, int] = {}
    id_to_name: dict[int, str] = {}
    for name, tid in CANONICAL_SKILL_MAPPING:
        if name in mapping and mapping[name] != tid:
            raise SystemExit(
                f"CANONICAL_SKILL_MAPPING: '{name}' has conflicting ids "
                f"{mapping[name]} and {tid}"
            )
        if tid in id_to_name and id_to_name[tid] != name:
            raise SystemExit(
                f"CANONICAL_SKILL_MAPPING: id {tid} is used by both "
                f"'{id_to_name[tid]}' and '{name}'"
            )
        mapping[name] = tid
        id_to_name[tid] = name
    return mapping


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
                        help="Override the embedded canonical mapping with an external "
                             "skill-mapping source. Accepts either a python "
                             "rename_skill_dirs_to_ids.py file or a TSV of "
                             "task_id<TAB>skill_name.  Pass multiple times to merge "
                             "sources.  When omitted, the built-in CANONICAL_SKILL_MAPPING "
                             "(group1 1..89 + group2 90..181) is used.")
    parser.add_argument("--only", default=None,
                        help="Comma-separated skill names to package (default: all discovered)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--strict-leak", action="store_true",
                        help="Exit non-zero if any task leaks its skill_name into agent-visible files")
    args = parser.parse_args()

    flat_dir = Path(args.flat_dir).resolve()
    if args.skill_map:
        skill_map_paths = [Path(p).resolve() for p in args.skill_map]
        base_map = merge_skill_mappings(skill_map_paths)
        map_sources = [str(p) for p in skill_map_paths]
        print(f"loaded merged mapping: {len(base_map)} skills from "
              f"{len(skill_map_paths)} external source(s)")
    else:
        base_map = canonical_mapping_dict()
        map_sources = ["<embedded CANONICAL_SKILL_MAPPING>"]
        print(f"loaded embedded canonical mapping: {len(base_map)} skills")

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
            "Add them to the canonical mapping before re-running.  Sources used:",
            *[f"  - {p}" for p in map_sources],
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
        "skill_map_sources": map_sources,
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
