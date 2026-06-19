#!/bin/bash
# SLURM wrapper for the group_hard_300 sweep: codex gpt-5.5 × 4 reasoning efforts,
# WITH-SKILL ONLY (no no-skill row).
#
# Submit from the REPO ROOT (the parent of skillsbench/):
#   cd <repo-root>            # the dir containing both skillsbench/ and benchflow/
#   sbatch skillsbench/experiments/sweep_hard.slurm
#
# One long job on one compute node: the Podman runtime + base image are set up
# once by the bootstrap, then each effort's config runs rollout + judge + aggregate
# for row 0 only (with_skills=true) over tasks_runtime/group_hard_300 (ids 182..515).

#SBATCH --account=cscc-users
#SBATCH -p cscc-cpu-p
#SBATCH -q cscc-cpu-qos
#SBATCH --cpus-per-task=80
#SBATCH --mem=220G
#SBATCH --time=24:00:00
#SBATCH --job-name=bf-sweep-hard
#SBATCH --output=logs/slurm/%x_%j.out
#SBATCH --error=logs/slurm/%x_%j.err

set -uo pipefail

# Rootless-Podman bootstrap (XDG_RUNTIME_DIR + podman service + node-local store +
# base image load + node/codex for judge + unset OPENAI_API_KEY). Inside sbatch,
# $0 is a spool copy; the repo lives at $SLURM_SUBMIT_DIR.
source "${SLURM_SUBMIT_DIR:-.}/slurm-podman-bootstrap.sh"
trap 'bf_podman_teardown' EXIT

docker compose version >/dev/null   # fail fast if provider/socket is wrong

cd "${SLURM_SUBMIT_DIR:-.}/skillsbench"

# Base image is loaded by the bootstrap; build only if somehow missing.
podman image exists skillsbench-base:latest \
  || docker build -t skillsbench-base:latest -f environment/Dockerfile.base environment/

CFG_DIR="experiments/configs/skill-eval/group_hard_300"
EFFORTS=(low medium high xhigh)

LOG_DIR="runs/_sweep_hard-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"
echo "sweep_hard log dir: $LOG_DIR"
echo "efforts:           ${EFFORTS[*]}"
echo "rows:              0 (with_skills ONLY)"
echo

SUMMARY=()
SWEEP_RC=0

for eff in "${EFFORTS[@]}"; do
  cfg="$CFG_DIR/codex-gpt5_5-$eff.yaml"
  log="$LOG_DIR/hard-$eff.log"

  echo "=== $(date '+%Y-%m-%d %H:%M:%S')  starting hard/$eff ==="
  echo "    config: $cfg"
  echo "    log:    $log"

  if [[ ! -f "$cfg" ]]; then
    echo "=== [SKIP] hard/$eff — config not found: $cfg ==="
    SUMMARY+=("$(printf '  %-14s rc=%s' "hard/$eff" "no-config")")
    SWEEP_RC=1
    echo
    continue
  fi

  # --rows 0  -> with_skills row only.  --keep-going  -> a row/effort failure
  # doesn't abort the rest of the sweep.
  uv run python3 skillsbench_x/run_experiment.py \
    --config "$cfg" \
    --rows 0 \
    --keep-going 2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  SUMMARY+=("$(printf '  %-14s rc=%s' "hard/$eff" "$rc")")

  if [[ $rc -eq 0 ]]; then
    echo "=== $(date '+%H:%M:%S')  [OK]   hard/$eff ==="
  else
    echo "=== $(date '+%H:%M:%S')  [FAIL] hard/$eff (rc=$rc) — continuing ==="
    SWEEP_RC=1
  fi
  echo
done

echo "=== $(date '+%Y-%m-%d %H:%M:%S')  sweep_hard done ==="
for line in "${SUMMARY[@]}"; do
  printf '%s\n' "$line"
done
echo "logs: $LOG_DIR"
exit $SWEEP_RC
