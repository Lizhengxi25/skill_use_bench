See [AGENTS.md](AGENTS.md) for project overview and contribution guidelines.

## ⚠️ Debugging the experiment harness (`skillsbench_x`) — READ FIRST

When debugging the rollout / LLM-as-judge pipeline, **do NOT run any config in
`experiments/configs/skill-eval/*.yaml`**, and do NOT run `skillsbench_x/run_experiment.py`
against them. Each config runs **every task × both skill variants** (rollout + judge +
aggregate) — far too slow and expensive for iteration.

Iterate on a **single task** with the per-stage drivers instead:

```bash
cd skillsbench
# rollout (one task).  On the server this must run inside a SLURM job that sources
# ../slurm-podman-bootstrap.sh — see docs/server-setup.md §F7. judge runs host-side.
python3 skillsbench_x/rollout.py \
  --tasks tasks_runtime/<group>/<task-id> --reasoning low --with-skills \
  -- --sandbox-user none

# judge (one task, host-side codex exec; reads runs/<run-dir>/<task-id>/trajectory.log)
python3 skillsbench_x/judge.py \
  --run runs/<run-dir> --rubric-root tasks_runtime/<group> --tasks <task-id>
```

Only run a full `skill-eval` config when the user explicitly asks for a real (non-debug) run.
