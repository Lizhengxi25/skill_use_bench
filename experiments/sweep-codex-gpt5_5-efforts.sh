#!/usr/bin/env bash
# Sequential sweep: codex gpt-5.5 × 4 reasoning efforts, both skill variants.
#
# Runs group1 first, then group2, in order — one `run_experiment.py` invocation
# per (group, effort).  Each invocation runs matrix rows 0,1 = with_skills AND
# no_skills (rollout + judge + aggregate per row).  `--keep-going` keeps a row /
# effort / group failure from cascading: the rest of the sweep still runs.
#
# A failed (group,effort) does NOT abort the sweep — we continue and surface a
# summary at the end.  Per-cell stdout/stderr is tee'd to
# runs/_sweep-<ts>/<group>-<effort>.log so live-tail and post-mortem both work.
#
# group1 configs:  experiments/configs/skill-eval/codex-gpt5_5-<effort>.yaml
# group2 configs:  experiments/configs/skill-eval/codex-gpt5_5-group2-<effort>.yaml
#
# Stays compatible with macOS's stock /bin/bash 3.2: no associative arrays, and
# we avoid the reserved `GROUPS` variable name (bash maintains that as the
# caller's unix group list and silently ignores assignments to it).

set -o pipefail

# Run from the skillsbench root regardless of where the script is invoked.
cd "$(dirname "$0")/.."

DATA_GROUPS=(group1 group2)
EFFORTS=(low medium high xhigh)

# Map a group label to its config-file path (group1 files keep their original
# names; group2 files carry a `group2` infix).
config_for() {  # $1=group  $2=effort
  case "$1" in
    group1) echo "experiments/configs/skill-eval/codex-gpt5_5-$2.yaml" ;;
    group2) echo "experiments/configs/skill-eval/codex-gpt5_5-group2-$2.yaml" ;;
    *)      echo "" ;;
  esac
}

LOG_DIR="runs/_sweep-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"
echo "sweep log dir: $LOG_DIR"
echo "groups:        ${DATA_GROUPS[*]}"
echo "efforts:       ${EFFORTS[*]}"
echo "rows:          0,1 (with_skills + no_skills)"
echo

# bash 3.2 has no associative arrays; collect per-cell summary lines in a plain
# indexed array instead.
SUMMARY=()
SWEEP_RC=0

for grp in "${DATA_GROUPS[@]}"; do
  for eff in "${EFFORTS[@]}"; do
    cfg="$(config_for "$grp" "$eff")"
    log="$LOG_DIR/$grp-$eff.log"
    cell="$grp/$eff"

    echo "=== $(date '+%Y-%m-%d %H:%M:%S')  starting $cell ==="
    echo "    config: $cfg"
    echo "    log:    $log"

    if [[ -z "$cfg" || ! -f "$cfg" ]]; then
      echo "=== [SKIP] $cell — config not found: $cfg ==="
      SUMMARY+=("$(printf '  %-16s rc=%s' "$cell" "no-config")")
      SWEEP_RC=1
      echo
      continue
    fi

    # tee so we see live output AND have a per-cell file for grep later.
    # PIPESTATUS[0] captures uv's rc — tee almost always succeeds so $? alone
    # would hide failures.
    uv run python3 skillsbench_x/run_experiment.py \
      --config "$cfg" \
      --rows 0,1 \
      --keep-going 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    SUMMARY+=("$(printf '  %-16s rc=%s' "$cell" "$rc")")

    if [[ $rc -eq 0 ]]; then
      echo "=== $(date '+%H:%M:%S')  [OK]   $cell ==="
    else
      echo "=== $(date '+%H:%M:%S')  [FAIL] $cell (rc=$rc) — continuing ==="
      SWEEP_RC=1
    fi
    echo
  done
done

echo "=== $(date '+%Y-%m-%d %H:%M:%S')  sweep done ==="
for line in "${SUMMARY[@]}"; do
  printf '%s\n' "$line"
done
echo "logs: $LOG_DIR"
exit $SWEEP_RC
