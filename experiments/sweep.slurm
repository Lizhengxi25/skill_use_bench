#!/bin/bash
# SLURM wrapper for the full codex gpt-5.5 effort sweep.
#
# Submit from the REPO ROOT (the parent of skillsbench/), so that
# $SLURM_SUBMIT_DIR points there and logs/slurm/ resolves:
#
#   cd <repo-root>            # the dir containing both skillsbench/ and benchflow/
#   sbatch skillsbench/experiments/sweep.slurm
#
# One long job: the Podman runtime + base image are set up once by the bootstrap,
# then the existing sweep script runs every (group, effort, skills) cell
# (rollout + judge + aggregate) on this single compute node.

#SBATCH --account=cscc-users
#SBATCH -p cscc-cpu-p
#SBATCH -q cscc-cpu-qos
#SBATCH --cpus-per-task=80
#SBATCH --mem=200G
#SBATCH --time=24:00:00
#SBATCH --job-name=bf-sweep
#SBATCH --output=logs/slurm/%x_%j.out
#SBATCH --error=logs/slurm/%x_%j.err

# NOT `set -e`: a single (group,effort) cell failure must not abort the whole sweep;
# the inner sweep-codex-gpt5_5-efforts.sh loop handles per-cell failures with --keep-going.
set -uo pipefail

# Rootless-Podman bootstrap (XDG_RUNTIME_DIR + podman service + node-local store +
# base image load + node/codex for judge). Inside sbatch, $0 is a spool copy; the
# repo lives at $SLURM_SUBMIT_DIR.
source "${SLURM_SUBMIT_DIR:-.}/slurm-podman-bootstrap.sh"
trap 'bf_podman_teardown' EXIT

docker compose version >/dev/null   # fail fast if provider/socket is wrong

bash "${SLURM_SUBMIT_DIR:-.}/skillsbench/experiments/sweep-codex-gpt5_5-efforts.sh"
