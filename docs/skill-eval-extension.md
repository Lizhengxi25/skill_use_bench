# Skill-eval extension

This repo is a soft fork of upstream SkillsBench tailored for an internal
research workflow that auto-synthesizes evaluation tasks and grades them with
LLM-as-judge over phased rubrics. The upstream Harbor task format, BenchFlow
SDK integration, and `bench …` CLI are kept as-is; this document covers only
the additions that live alongside.

## What's new

All extension code is namespaced under `skillsbench_x/` and supporting layout:

```
professional/skillsbench/
├── environment/
│   └── Dockerfile.base                   # NEW shared base image (ubuntu + node + python + pytest + rg)
├── skillsbench_x/                        # NEW extension package
│   ├── cli.py                            # unified `skillsbench-x <rollout|judge|aggregate>` entry
│   ├── rollout.py                        # BenchFlow-driven rollout with ACP→text normalizer
│   ├── rollout_direct.py                 # fallback: drives `codex exec` directly (no Docker)
│   ├── judge.py                          # LLM-as-judge driver, batched
│   ├── aggregate.py                      # subprocess wrapper around analyze_grades.py
│   ├── analyze_grades.py                 # cascade scoring (vendored from
│   │                                     # ../../../test/analyze_grades.py so
│   │                                     # skillsbench is self-contained)
│   ├── from_flat.py                      # HuggingFace flat-format → Harbor tasks/ adapter
│   └── run_experiment.py                 # YAML-driven matrix orchestrator (one-click)
├── tasks/<int>/                          # NEW skill-eval tasks, integer-ID slugs
│   ├── task.toml                         # category="skill-eval", hidden_skill_name="..."
│   ├── instruction.md                    # auto-synthesized user query
│   ├── environment/
│   │   ├── Dockerfile                    # FROM skillsbench-base:latest + COPY files /app/
│   │   ├── files/                        # env files placed at /app inside the container
│   │   └── skills/<skill_real_name>/     # SKILL.md (name: untouched) + companions
│   ├── rubric/                           # NEW per-task LLM-judge artifacts
│   │   ├── rubric.json
│   │   └── judge_phase_{skill_identification,module_sequence,post_processing}.md
│   └── tests/test.sh                     # NO-OP verifier — scoring happens in skillsbench_x/judge.py
├── runs/<run_id>/<task_id>/              # NEW rollout outputs (gitignore this)
└── grades/<grade_id>/<task_id>/          # NEW judge outputs (gitignore this)
```

Upstream files (`README.md`, `AGENTS.md`, `CONTRIBUTING.md`, `taxonomy.md`,
`taxonomy.yaml`, `docs/`, `tasks_excluded/`, `experiments/`, `website/`,
`pyproject.toml`, `uv.lock`) are kept as inherited from skillsbench@main with
only minimal additive edits where our new vocabulary needs to validate.

## Why extension, not fork

Tried four shapes during scoping. The pros/cons matter for the next change:

1. **Custom thin runner** that shells out to `codex exec` (no Docker). Fastest
   to iterate, but no sandbox so the agent can `cd ../..` out of the task.
   Kept as `rollout_direct.py` for cheap local debugging.
2. **Per-task Docker image rebuilds** for each task. Storage explodes for 181
   tasks; image cache busted on every env-file edit.
3. **Shared base image + per-task `FROM skillsbench-base + COPY files /app/`**.
   One 693MB base, ~10–50KB per-task layer, ~1s build per task. Adopted.
4. **Replace BenchFlow entirely**. Throws away docker-compose, agent-harness
   adapters, ACP trajectory capture, sandbox-user lockdown, daytona backend.
   Not worth the code volume.

BenchFlow + shared base wins because the only things we need to override are:

- **Where skills mount per harness.** BenchFlow already handles this — it
  symlinks `/skills/` to `/root/.claude/skills/`, `/root/.codex/skills/`,
  `/root/.agents/skills/`, `/root/.opencode/skills/` *all at once*. One
  `--skills-dir` flag covers every harness. No override needed.
- **Verifier semantics.** Upstream expects `tests/test.sh` to write a 0/1
  reward; we want LLM-as-judge instead. Resolved by making `test.sh` a no-op
  that writes `1` so BenchFlow records the rollout cleanly; real scoring runs
  outside the container against the captured trajectory.
- **Re-grade without re-rollout.** Not supported upstream. Resolved by
  keeping the trajectory on the host (BenchFlow already exports it to
  `agent/acp_trajectory.jsonl`) and running `skillsbench_x/judge.py` as a
  standalone step that reads from `runs/` and writes to `grades/`.

## Three-stage flow

After data is ready (either by running `skill_eval/test_case_search/` end-to-end
locally, or by cloning a prepackaged dataset from HuggingFace), the pipeline is:

```bash
# 0) one-time: build the shared base image
docker build -f environment/Dockerfile.base -t skillsbench-base:latest environment/

# 1) adapt HF flat-format dump → Harbor tasks/
python3 skillsbench_x/from_flat.py \
  --flat-dir ./hf_clone \
  --output-root tasks_runtime \
  --run-id 20260601-g1

# 2) rollout (codex or claude-code; with or without skills)
python3 skillsbench_x/rollout.py \
  --tasks tasks_runtime/20260601-g1 \
  --harness codex \
  --with-skills \
  --run-id 20260601-codex-with \
  --concurrency 10 \
  -- --sandbox-user none

# 3) judge (re-runnable; rubric edits only require this + step 4)
python3 skillsbench_x/judge.py \
  --run runs/20260601-codex-with \
  --rubric-root tasks_runtime/20260601-g1 \
  --grade-id 20260601-codex-with--rev1 \
  --concurrency 30

# 4) aggregate
python3 skillsbench_x/aggregate.py \
  --grades grades/20260601-codex-with--rev1 --md
```

Stages 3 and 4 are cheap and re-runnable. Rubric tweaks only trigger them.

### One-click YAML runs

The four-stage chain is wrapped by `skillsbench_x/run_experiment.py`, which
reads an experiment YAML describing a (harness × with-skills × model × …)
matrix and dispatches each row through rollout → judge → aggregate in turn.

```bash
# Run the full matrix declared in the YAML:
uv run python3 skillsbench_x/run_experiment.py \
    --config experiments/configs/skill-eval/group1-codex.yaml

# Re-grade-only after rubric edits (bump `rev:` in the YAML first):
uv run python3 skillsbench_x/run_experiment.py \
    --config experiments/configs/skill-eval/group1-codex.yaml \
    --only judge,aggregate

# Dry-run the plan + restrict to one matrix row:
uv run python3 skillsbench_x/run_experiment.py \
    --config experiments/configs/skill-eval/group1-codex.yaml \
    --dry-run --rows 0
```

The YAML is opinionated for this pipeline; required keys are `name`,
`tasks_root`, and a non-empty `matrix:` list whose rows specify `harness` and
`with_skills` (model/agent/reasoning are optional overrides). `rollout`,
`judge`, and `aggregate` blocks tune concurrency, judge reasoning effort,
and aggregate output format. `run_id` and `grade_id` are derived from
`run_id_template` and `grade_id_template`, defaulting to
`{name}-{harness}-{skills}` and `{run_id}--rev{rev}`.

A matrix row's `reasoning:` field is forwarded to BenchFlow as
`bench run --reasoning-effort <value>` (see the
[Reasoning-effort sweeps](#reasoning-effort-sweeps) section below) — only the
codex-acp adapter honors it today, courtesy of the local
`feat/reasoning-effort` patch.

`uv run` is needed because pyyaml lives in the project venv; the underlying
rollout/judge/aggregate scripts are stdlib-only and can still be invoked
directly with `python3`.

## Harness ↔ model defaults

Set in `skillsbench_x/rollout.py:DEFAULT_HARNESS_*`. Override per run with
`--agent` and `--model`:

| `--harness`    | BenchFlow agent     | Default model         | Skill mount path inside container |
|----------------|---------------------|-----------------------|-----------------------------------|
| `codex`        | `codex-acp`         | `gpt-5.5`             | `/root/.agents/skills/<name>/`    |
| `claude-code`  | `claude-agent-acp`  | `claude-sonnet-4-6`   | `/root/.claude/skills/<name>/`    |

BenchFlow symlinks the same skills dir to ALL agent-specific paths, so a single
`--skills-dir` deploys correctly for any harness without env mutation.

`--with-skills` decides whether the flag is passed at all. No-skills runs
get a cleanly skill-less container — no `find -name .claude -delete` hacks.

## Per-task layout, mapped to factory output

For each successfully synthesized skill in the factory:

| Factory artifact (in `skill_eval/test_case_search/test/skills_200_g1_run/<skill>/`)             | Harbor destination (`tasks/<id>/…`)                |
|--------------------------------------------------------------------------------------------------|-----------------------------------------------------|
| `<skill>/{SKILL.md, AGENTS.md, README.md, assets/, references/, scripts/}`                       | `environment/skills/<skill>/…`                      |
| `<skill>/<TIMESTAMP>/query_generation/revised_user_need.md`                                      | `instruction.md`                                    |
| `<skill>/<TIMESTAMP>/stage1/.../environment/<*>`                                                 | `environment/files/<*>`                             |
| `<skill>/<TIMESTAMP>/rubric_generation/rubric.json`                                              | `rubric/rubric.json`                                |
| `<skill>/<TIMESTAMP>/rubric_generation/judge_phase_*.md`                                         | `rubric/judge_phase_*.md`                           |
| (generated by adapter)                                                                            | `task.toml`, `environment/Dockerfile`, `tests/test.sh` |

The factory's `--format harbor` (in `skill_eval/test_case_search/package_outputs.sh`)
does this locally; `skillsbench_x/from_flat.py` does it server-side from a
flat-format HF clone.

## Task ID mapping rules (`from_flat.py`)

Task IDs are fixed by two canonical mapping files in the user's eval area:

- `test/group1/no_skills/latest_read_only/rename_skill_dirs_to_ids.py` (IDs 1–89)
- `test/group2/no_skills/lave_cc_sn_hg/rename_skill_dirs_to_ids.py` (IDs 90–181)

Merged at load time. Conflict detection: same name → different IDs across
sources, OR same ID → different names, both abort.

Adapter behavior on the merged mapping:

- **Mapping entry has no HF data**: silently skipped. The mapping is the
  registry, not the seed list.
- **HF skill not in mapping**: hard error. IDs are never auto-allocated;
  the offending skill must be added to a mapping source first.

Override the source list with `--skill-map path` (repeatable, accepts `.py` or
TSV `task_id<TAB>skill_name`).

## Anti-leakage guarantees

The agent must not learn the skill's name from anything it can read. Three
safety rails:

1. **Task slug is the integer ID** (`tasks/7/`, not `tasks/accelint-react-testing/`).
2. **Workdir filenames** (under `environment/files/`) and `instruction.md` are
   grepped for the skill name at packaging time; hits print a `LEAK!` warning,
   `--strict-leak` makes them fatal.
3. **SKILL.md `name:` frontmatter is preserved verbatim** — the skill loader
   inside the harness needs the real name to discover the skill. This is
   acceptable because the agent only sees skill names *after* it chooses to
   browse `/root/.{claude,agents}/skills/`, which is itself a graded behavior
   (rubric criterion: did the agent read SKILL.md).

## ACP trajectory normalization — capabilities and limits

`rollout.py:jsonl_to_text` converts BenchFlow's `agent/acp_trajectory.jsonl`
into a flat text the judge can read.

**What survives:**
- `user_message`, `agent_message`, `agent_thought` full text.
- `tool_call` `kind`, `title`, `status`, `tool_call_id`.
- For `kind=execute` / `kind=edit`: `title` typically carries the FULL shell
  command or `apply_patch` heredoc, so the script's logic is graded directly.

**What doesn't:**
The `@zed-industries/codex-acp` wrapper (and currently `claude-agent-acp` per
spot-check) never populates the ACP `content[]` array on `tool_call_update`
events. BenchFlow's `acp/session.py:131` and `trajectories/_capture.py:38` are
ready to receive it — the field is simply empty at the wire. Verified across
41 events of a real codex-acp run and 28 events of a partial run; no event of
any kind had non-empty `content`.

Consequence:
- `kind=read`: you see *which file* was read, not its contents.
- `kind=search`: you see the regex/grep pattern, not the matched lines.
- `kind=execute`/`kind=edit`: rescued via `title`.

`rollout.py:ACP_TRAJECTORY_PREAMBLE` is prepended to every normalized
trajectory so the judge knows this limitation and avoids penalizing the agent
for missing tool output. Per-kind caps in `RESULT_CAP_BY_KIND` keep the
trajectory token-cost predictable.

**Measured impact on a real task** (task 7 = `accelint-react-testing`):

| Trajectory source        | Score   | Notes |
|--------------------------|---------|-------|
| direct codex stdout      | 8/15    | reference |
| codex-acp, no preamble   | 5/15    | -3 from missing `command_output` evidence |
| codex-acp, w/ normalizer | 6/16    | preamble recovers PP-ACT-1; M5/M6/M7 still lost |

The residual −2 to −3 gap is structural to codex-acp until the upstream
wrapper populates `content`. **Cross-harness comparisons (codex-acp ↔
claude-agent-acp) and within-harness comparisons (with-skills ↔ no-skills)
remain fair**; direct codex scores are *not* comparable to codex-acp scores.

## Cost and concurrency

| Stage                 | Per-task cost                          | Bottleneck                | Suggested concurrency  |
|-----------------------|-----------------------------------------|---------------------------|------------------------|
| rollout (codex-acp)   | $0.3–0.8 + 5–10 min, ~600 MB RAM (cap 4 GB) | local Docker + RAM        | 8–12 on 32 GB Mac, 20–30 on 64 GB Linux |
| judge (3 phases)      | $0.03–0.10 (with prompt caching)       | OpenAI/Anthropic API tier | 30–50 host processes   |
| aggregate             | <0.1s                                   | none                      | n/a                    |

Per-task numbers measured on smoke-bf-codex (task 7, gpt-5.5, low reasoning).
The 4 GB `task.toml memory_mb` is a cgroup cap; typical actual usage is
~200–600 MB.

**Trajectory size:** ~15–35 KB per task after normalization. A 181-task
re-grade run costs ~$7 with cache hits, ~$15 without.

## Smoke task 7 — example to copy

`tasks/7/` is the worked example we built end-to-end during the scoping
phase. Use it as a reference for:
- shape of `task.toml` (note `hidden_skill_name`)
- shape of `environment/Dockerfile` (single line + COPY)
- shape of `tests/test.sh` (no-op verifier)

Don't ship 7 in production datasets — it leaks the skill name as the working
file basenames are React test files specific to one real OSS library, which
overlaps the rubric's audit criteria. Generated tasks from
`skill_eval/test_case_search/` don't have this issue.

## Reasoning-effort sweeps

Added 2026-05-29. The codex-acp adapter in upstream BenchFlow has no
first-class reasoning-effort plumbing — `skillsbench_x/rollout.py`'s
`--reasoning` flag used to print a WARNING and no-op. The local BenchFlow
fork at `../benchflow` carries a patch on branch `feat/reasoning-effort`
that surfaces it end-to-end:

- `bench run --reasoning-effort minimal|low|medium|high|xhigh`
- `SDK.run(reasoning_effort=...)`
- `RolloutConfig(reasoning_effort=...)` — validated in `__post_init__`,
  unsupported agents raise at `Rollout.setup` (codex-acp is the only one
  wired so far via `AgentConfig.reasoning_effort_flag="-c model_reasoning_effort={value}"`)

`skillsbench_x/rollout.py` now forwards `--reasoning <eff>` →
`bench run --reasoning-effort <eff>` and writes the effort into
`metadata.tsv` and `manifest.json`.

### How to run a sweep

Per-effort YAML configs live at
`experiments/configs/skill-eval/codex-gpt5_5-{low,medium,high,xhigh}.yaml`.
Each has a distinct `name:` so run_id/grade_id directories don't collide
(`group1-codex-{effort}-codex-{with,no}`). The matrix rows pin
`reasoning:` on both the with-skills and no-skills rows so the only
varying axis within a single config is skill availability, not effort.

One-shot driver:

```bash
./experiments/sweep-codex-gpt5_5-efforts.sh
```

It tee's each effort's stdout to `runs/_sweep-<ts>/<effort>.log`, does NOT
abort on per-effort failure (continues, summarizes rc at the end), and
exits non-zero if any effort failed.

### Known gotchas

1. **`minimal` is rejected by gpt-5.5.** The flag arrives at codex-acp
   correctly (verified via `bench.log`'s `ContainerTransport: agent started`
   line including `-c model_reasoning_effort=minimal`), but Codex/the model
   returns `ACP error -32603: Internal error` with 0 tool calls. Other
   models may accept it; `REASONING_EFFORT_VALUES` in the patch keeps
   `minimal` as a valid value so model-specific compatibility doesn't bake
   into the validator. The user's chosen 4-effort sweep is
   `low / medium / high / xhigh`, which dodges this.

2. **`run_experiment.py`'s aggregate stage crashes.** The orchestrator
   passes `cmd.append("--md")` with no value, but `aggregate.py`'s argparse
   requires `--md <path>`. All four sweep runs hit
   `aggregate.py: error: argument --md: expected one argument` — `judge`
   succeeds and writes per-task phase JSONs, but no `aggregate.md` lands.
   Workaround: invoke aggregation manually after the sweep:
   ```bash
   uv run python skillsbench_x/aggregate.py \
     --grades grades/group1-codex-<eff>-codex-with--rev1 --per-task
   ```
   That prints the summary to stdout. Fix candidate (not landed yet): in
   `run_experiment.py:build_aggregate_cmd`, change `cmd.append("--md")` to
   `cmd += ["--md", str(grade_dir / "aggregate.md")]`.

3. **`xhigh` was not part of the initial A/B**. Both `low` and `high` were
   end-to-end verified on task 11 (tool calls 6 → 10, agent_execution
   120 s → 150 s, traj 15 KB → 18 KB). `medium`/`xhigh` haven't been
   bisected against a single task, but the full 87-task sweep completed
   for all four (84/85 graded for xhigh — one task ungraded, cause not
   investigated).

## References

- Upstream Harbor format and `bench` CLI: see `AGENTS.md`, `CONTRIBUTING.md`.
- BenchFlow (current state): editable local path `../benchflow`, branch
  [`feat/reasoning-effort`](https://github.com/Lizhengxi25/benchflow_reasoning_efforts_patch/tree/feat/reasoning-effort)
  off upstream
  [`benchflow-ai/benchflow @ 7a479e9`](https://github.com/benchflow-ai/benchflow/tree/7a479e9).
  See the README "BenchFlow dependency" callout for switching back to a
  pinned remote rev.
- Trajectory + ACP capture: `benchflow/trajectories/_capture.py`,
  `benchflow/acp/session.py` (inside `.venv/` or the local fork).
- The user's data factory: `skill_eval/test_case_search/` (NOT in this repo;
  the factory lives one level up).
- The cascade scorer: `skillsbench_x/analyze_grades.py` (vendored from
  `../../../test/analyze_grades.py` so skillsbench has no parent-dir
  dependency for scoring). Re-sync upstream by re-copying the file when the
  cascade-scoring logic changes; the CLI contract is the stable seam.
