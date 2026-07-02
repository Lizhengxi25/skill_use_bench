#!/usr/bin/env bash
# Build + OFFLINE-verify the 16 Tier-2 / C-class task images (env_deploy_tier2_C_plan.md).
#
#   bash experiments/build_verify_tier2c.sh [task ...]     # default: all 16
#   ENG=podman bash experiments/build_verify_tier2c.sh ...  # server (rootless podman)
#
# Each task: build <task>/environment/Dockerfile (which runs setup.sh at build time),
# then run a per-task check with `--network none` to prove the agent's toolchain + the
# build-time-warmed dependency cache work OFFLINE. Internet=true tasks (auth-security,
# when-building-backend-api) only need the toolchain offline; their deps may pull at runtime.
set -uo pipefail
cd "$(dirname "$0")/.."                      # -> skillsbench/ repo root
ENG=${ENG:-docker}
DIR=tasks_runtime/search-env-tier2c
LOGDIR=$(mktemp -d)
echo "engine=$ENG  taskdir=$DIR  logs=$LOGDIR"

# per-task offline check (run as `bash -lc` inside the built image, cwd=/app)
verify_cmd () { case "$1" in
  advanced-ds-library)        echo 'java -version && javac -version' ;;
  auth-security)              echo 'go version && go env GOTOOLCHAIN' ;;
  oauth2-authentication)      echo 'go version && cd /app/hydra-*/ && go build ./oauth2/... 2>&1 | tail -3' ;;
  moai-1)                     echo 'go version && (golangci-lint version 2>&1 | head -1) && cd /app/moai-adk-*/ && go build ./... 2>&1 | tail -3' ;;
  module-systems)             echo 'go version && cd /app/extracted/esbuild-*/ && go build ./... 2>&1 | tail -3' ;;
  growing-outside-in-systems) echo 'go version && cd /app/extracted/wild-workouts-go-ddd-example-*/ && go build ./... 2>&1 | tail -3' ;;
  designing-distributed-systems) echo 'go version && cd /app/extracted/microservices-demo-*/src/checkoutservice && go build ./... 2>&1 | tail -3' ;;
  advanced-ds-library) echo 'java -version' ;;
  full-stack-orchestration-full-stack-feature) echo 'java -version && cd /app/extracted/jhipster-sample-app-*/ && test -d node_modules && echo node_modules-baked && ./mvnw -o -ntp -v 2>&1 | head -2' ;;
  when-building-backend-api-orchestrate-api-development) echo 'java -version && cd /app/extracted/spring-petclinic-rest-*/ && ./mvnw -o -ntp -v 2>&1 | head -2' ;;
  data-schema-knowledge-modeling) echo 'dotnet --version && cd /app/extracted/chinook-database-*/ && dotnet build ChinookDatabase.sln --no-restore 2>&1 | tail -3' ;;
  database-architect-1)       echo 'dotnet --version && cd /app/extracted/chinook-database-*/ && dotnet build ChinookDatabase.sln --no-restore 2>&1 | tail -3' ;;
  Bytecode-VM)                echo 'rustc --version && cd /app/extracted/boa-*/ && cargo metadata --offline >/dev/null && echo cargo-offline-ok' ;;
  interpreters)               echo 'dart --version && cd /app/extracted/craftinginterpreters-*/ && make clox && ls -l build/clox' ;;
  typescript-ops)             echo 'node --version && pnpm --version && cd /app/extracted/zod-*/ && test -d node_modules && echo node_modules-baked' ;;
  azure-architecture-autopilot) echo 'az bicep version' ;;
  issue-review)               echo 'python3.9 --version && python3.9 -c "import marshmallow; print(marshmallow.__version__)"' ;;
  *) echo 'echo "no verify defined"; exit 2' ;;
esac }

# base image
if ! $ENG image inspect skillsbench-base:latest >/dev/null 2>&1; then
  echo "=== building skillsbench-base:latest ==="
  $ENG build -t skillsbench-base:latest -f environment/Dockerfile.base environment/ 2>&1 | tail -5 || { echo "BASE BUILD FAILED"; exit 1; }
fi

TASKS=("$@"); [ ${#TASKS[@]} -eq 0 ] && TASKS=($(ls "$DIR" | grep -vE '^manifest.json$'))
declare -a PASS FAIL
for t in "${TASKS[@]}"; do
  echo; echo "######## $t ########"
  img="sbx-$(echo "$t" | tr '[:upper:]' '[:lower:]')"
  if $ENG build -t "$img" -f "$DIR/$t/environment/Dockerfile" "$DIR/$t/environment/" >"$LOGDIR/$t.build.log" 2>&1; then
    echo "  build OK"
    if $ENG run --rm --network none "$img" bash -lc "$(verify_cmd "$t")" >"$LOGDIR/$t.verify.log" 2>&1; then
      echo "  offline-verify OK"; tail -2 "$LOGDIR/$t.verify.log" | sed 's/^/    /'; PASS+=("$t")
    else
      echo "  offline-verify FAIL (see $LOGDIR/$t.verify.log)"; tail -5 "$LOGDIR/$t.verify.log" | sed 's/^/    /'; FAIL+=("$t")
    fi
  else
    echo "  build FAIL (see $LOGDIR/$t.build.log)"; tail -8 "$LOGDIR/$t.build.log" | sed 's/^/    /'; FAIL+=("$t")
  fi
done
echo; echo "===== SUMMARY ====="
echo "PASS (${#PASS[@]}): ${PASS[*]:-}"
echo "FAIL (${#FAIL[@]}): ${FAIL[*]:-}"
echo "logs: $LOGDIR"
