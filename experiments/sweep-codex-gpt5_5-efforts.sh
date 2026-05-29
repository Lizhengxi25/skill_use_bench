#!/usr/bin/env bash
# Sequential sweep: codex gpt-5.5 × 4 reasoning efforts, with_skills only.
#
# Each YAML's matrix is [with_skills=true (row 0), with_skills=false (row 1)],
# so `--rows 0` runs only the with_skills variant.  rollout + judge + aggregate
# run in one shot per effort, so this script writes the full sweep end-to-end.
#
# A failed effort does NOT abort the sweep — we continue and surface a summary
# at the end.  Per-effort stdout/stderr is tee'd to runs/_sweep-<ts>/<effort>.log
# so live-tail and post-mortem are both available.

set -o pipefail

# Run from the skillsbench root regardless of where the script is invoked.
cd "$(dirname "$0")/.."

EFFORTS=(low medium high xhigh)

LOG_DIR="runs/_sweep-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"
echo "sweep log dir: $LOG_DIR"
echo "efforts:       ${EFFORTS[*]}"
echo

# Track per-effort rc so we can summarize at the end.
declare -A RC
SWEEP_RC=0

for eff in "${EFFORTS[@]}"; do
  cfg="experiments/configs/skill-eval/codex-gpt5_5-$eff.yaml"
  log="$LOG_DIR/$eff.log"

  echo "=== $(date '+%Y-%m-%d %H:%M:%S')  starting effort=$eff ==="
  echo "    config: $cfg"
  echo "    log:    $log"

  # tee so we see live output AND have a per-effort file for grep later.
  # PIPESTATUS[0] captures uv's rc — tee almost always succeeds so $? alone
  # would hide failures.
  uv run python3 skillsbench_x/run_experiment.py \
    --config "$cfg" \
    --rows 0 2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  RC[$eff]=$rc

  if [[ $rc -eq 0 ]]; then
    echo "=== $(date '+%H:%M:%S')  [OK]   effort=$eff ==="
  else
    echo "=== $(date '+%H:%M:%S')  [FAIL] effort=$eff (rc=$rc) — continuing ==="
    SWEEP_RC=1
  fi
  echo
done

echo "=== $(date '+%Y-%m-%d %H:%M:%S')  sweep done ==="
for eff in "${EFFORTS[@]}"; do
  printf '  %-7s rc=%s\n' "$eff" "${RC[$eff]}"
done
echo "logs: $LOG_DIR"
exit $SWEEP_RC
