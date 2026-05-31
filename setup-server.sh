#!/usr/bin/env bash
#
# setup-server.sh — 无 sudo 环境配置脚本，用于把 SkillsBench 实验（rollout + LLM-as-judge）
# 部署到一台 Ubuntu 24.04 服务器上，容器运行时是 *rootless Podman*（`docker` 是 podman-docker 壳）。
#
# 全部用户级安装，零 sudo。脚本只装环境、绝不写入任何真实密钥/订阅令牌。
# 详见 docs/server-setup.md。
#
# 用法:
#   bash setup-server.sh [--with-claude] [--skip-build] [--help]
#
#   --with-claude   额外安装 @anthropic-ai/claude-code（仅当你要跑 claude-code 行时需要）
#   --skip-build    跳过 `docker build skillsbench-base:latest`（基础镜像构建较慢，且需联网）
#
# 幂等：已安装的步骤会自动跳过，可反复运行。
set -uo pipefail

# ── 解析参数 ─────────────────────────────────────────────────────────────
WITH_CLAUDE=0
SKIP_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --with-claude) WITH_CLAUDE=1 ;;
    --skip-build)  SKIP_BUILD=1 ;;
    -h|--help)
      sed -n '3,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $arg（用 --help 查看用法）" >&2; exit 2 ;;
  esac
done

# ── 路径与日志 ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLSBENCH_DIR="$SCRIPT_DIR"
BENCHFLOW_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/benchflow"
BASHRC="$HOME/.bashrc"

c_ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }
c_info() { printf '\033[36m• %s\033[0m\n' "$*"; }
c_warn() { printf '\033[33m⚠ %s\033[0m\n' "$*" >&2; }
c_err()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; }
step()   { printf '\n\033[1;35m── %s ──\033[0m\n' "$*"; }

WARNINGS=()
warn() { c_warn "$*"; WARNINGS+=("$*"); }

# 把一行 export 安全地追加进 ~/.bashrc（去重）
ensure_bashrc_line() {
  local line="$1"
  touch "$BASHRC"
  grep -qsF -- "$line" "$BASHRC" || printf '%s\n' "$line" >> "$BASHRC"
}

# 让 ~/.local/bin 在本次脚本运行内也生效
mkdir -p "$HOME/.local/bin"
export PATH="$HOME/.local/bin:$PATH"

# ── 架构探测（compose / node 二进制选择）─────────────────────────────────
UNAME_M="$(uname -m)"
case "$UNAME_M" in
  x86_64|amd64)  COMPOSE_ARCH="x86_64" ;;
  aarch64|arm64) COMPOSE_ARCH="aarch64" ;;
  *) c_err "不支持的架构: $UNAME_M"; exit 1 ;;
esac

step "0/8 预检：容器运行时（rootless Podman / docker 壳）"
if ! command -v docker >/dev/null 2>&1; then
  c_err "找不到 docker（应为 podman-docker 壳）。请确认服务器已提供 rootless Podman，再重跑。"
  exit 1
fi
if docker info >/dev/null 2>&1; then
  RUNTIME_DESC="$(docker version 2>/dev/null | grep -iE 'podman|engine' | head -1 | tr -s ' ' || true)"
  c_ok "docker info 通过（${RUNTIME_DESC:-容器运行时可用}）"
  PODMAN_SOCK="/run/user/$(id -u)/podman/podman.sock"
  [ -S "$PODMAN_SOCK" ] && c_info "Podman socket: $PODMAN_SOCK" \
                        || warn "未发现 Podman socket（$PODMAN_SOCK）；方案 P 一般仍可用，方案 D 才依赖它。"
else
  c_err "docker info 失败。rootless Podman 似乎未就绪——请先确保 'docker info' 能正常输出。"
  exit 1
fi

step "1/8 安装 uv（Python 包管理 / 任务运行）"
if command -v uv >/dev/null 2>&1; then
  c_ok "uv 已安装：$(uv --version)"
else
  c_info "安装 uv ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv 安装到 ~/.local/bin；本会话内补进 PATH
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 && c_ok "uv 安装完成：$(uv --version)" \
                                || { c_err "uv 安装失败"; exit 1; }
fi
ensure_bashrc_line 'export PATH="$HOME/.local/bin:$PATH"'

step "2/8 安装 Node（nvm，纯用户级）"
export NVM_DIR="$HOME/.nvm"
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  c_info "安装 nvm ..."
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
fi
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
if command -v nvm >/dev/null 2>&1; then
  if ! nvm which 22 >/dev/null 2>&1; then
    c_info "安装 Node 22 ..."
    nvm install 22
  fi
  nvm use 22 >/dev/null 2>&1 || true
  nvm alias default 22 >/dev/null 2>&1 || true   # 新登录 shell 也默认用 node 22，确保 codex 常驻 PATH
  c_ok "Node：$(node --version 2>/dev/null)  npm：$(npm --version 2>/dev/null)"
else
  c_err "nvm 不可用，无法安装 Node。请检查网络后重试。"
  exit 1
fi

step "3/8 安装宿主机 codex CLI（judge 必需）"
if command -v codex >/dev/null 2>&1; then
  c_ok "codex 已安装：$(codex --version 2>/dev/null || echo '?')"
else
  c_info "npm install -g @openai/codex ..."
  npm install -g @openai/codex
  command -v codex >/dev/null 2>&1 && c_ok "codex 安装完成：$(codex --version 2>/dev/null)" \
                                   || warn "codex 安装后未在 PATH 找到，请检查 nvm 全局 bin 是否在 PATH。"
fi
if [ "$WITH_CLAUDE" = "1" ]; then
  if command -v claude >/dev/null 2>&1; then
    c_ok "claude 已安装：$(claude --version 2>/dev/null || echo '?')"
  else
    c_info "npm install -g @anthropic-ai/claude-code ..."
    npm install -g @anthropic-ai/claude-code || warn "claude-code 安装失败（仅影响 claude-code 行）。"
  fi
fi

step "4/8 配置 docker compose provider（Podman 默认没有，benchflow 硬依赖）"
COMPOSE_BIN="$HOME/.local/bin/docker-compose"
if [ -x "$COMPOSE_BIN" ]; then
  c_ok "docker-compose provider 已存在：$("$COMPOSE_BIN" version --short 2>/dev/null || echo present)"
else
  c_info "下载 Docker Compose v2 独立二进制 ..."
  if curl -fsSL -o "$COMPOSE_BIN" \
       "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${COMPOSE_ARCH}"; then
    chmod +x "$COMPOSE_BIN"
    c_ok "docker-compose 安装完成：$("$COMPOSE_BIN" version --short 2>/dev/null || echo present)"
  else
    warn "下载 docker-compose 失败（网络？）。可手动放置到 $COMPOSE_BIN 后重跑。"
  fi
fi
# 让 podman 的 compose 子命令使用上面的 provider
CONTAINERS_CONF="$HOME/.config/containers/containers.conf"
mkdir -p "$(dirname "$CONTAINERS_CONF")"
if [ -f "$CONTAINERS_CONF" ] && grep -qsE '^\s*compose_providers' "$CONTAINERS_CONF"; then
  c_ok "containers.conf 已配置 compose_providers"
else
  c_info "写入 containers.conf 的 compose 设置 ..."
  cat >> "$CONTAINERS_CONF" <<EOF

# Added by skillsbench setup-server.sh — 让 'docker compose' 走用户级 docker-compose v2
[engine]
compose_providers = ["$COMPOSE_BIN"]
compose_warning_logs = false
EOF
  c_ok "已写入 $CONTAINERS_CONF"
fi
# 自检
if docker compose version >/dev/null 2>&1; then
  c_ok "docker compose 可用：$(docker compose version 2>/dev/null | head -1)"
else
  warn "'docker compose version' 自检失败。若 rollout 报 compose 相关错误，请改用 docs/server-setup.md 的【方案 D】。"
fi

step "5/8 确保 ~/.codex/config.toml 的判分模型 model = \"gpt-5.5\""
CODEX_CONFIG="$HOME/.codex/config.toml"
mkdir -p "$HOME/.codex"; chmod 700 "$HOME/.codex" 2>/dev/null || true
if [ ! -f "$CODEX_CONFIG" ]; then
  printf 'model = "gpt-5.5"\n' > "$CODEX_CONFIG"
  c_ok "已创建 $CODEX_CONFIG（model = gpt-5.5）"
elif grep -qE '^\s*model\s*=' "$CODEX_CONFIG"; then
  c_ok "config.toml 已有顶层 model：$(grep -E '^\s*model\s*=' "$CODEX_CONFIG" | head -1)"
else
  # 顶层 key 必须出现在任何 [table] 之前 → 前置写入，保证 TOML 合法
  { printf 'model = "gpt-5.5"\n'; cat "$CODEX_CONFIG"; } > "$CODEX_CONFIG.tmp" && mv "$CODEX_CONFIG.tmp" "$CODEX_CONFIG"
  c_ok "已在 config.toml 顶部加入 model = gpt-5.5"
fi

step "6/8 检查 benchflow 同级目录与分支"
if [ -d "$BENCHFLOW_DIR/.git" ]; then
  BF_BRANCH="$(git -C "$BENCHFLOW_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  if [ "$BF_BRANCH" = "feat/reasoning-effort" ]; then
    c_ok "benchflow 在 feat/reasoning-effort 分支：$BENCHFLOW_DIR"
  else
    warn "benchflow 当前分支是 '$BF_BRANCH'，应为 'feat/reasoning-effort'（带 --reasoning-effort patch）。"
    warn "  cd $BENCHFLOW_DIR && git checkout feat/reasoning-effort"
  fi
else
  c_err "未找到同级 benchflow 仓库：$BENCHFLOW_DIR"
  c_err "  skillsbench 依赖 ../benchflow（editable path）。请把 benchflow 放到 skillsbench 的同级目录。"
  exit 1
fi

step "7/8 uv sync（跳过 dev，避开 skills-ref 的 git 依赖）"
( cd "$SKILLSBENCH_DIR" && uv sync --no-dev ) \
  && c_ok "uv sync 完成" \
  || { c_err "uv sync 失败（看上面的报错；常见原因：benchflow 不在同级 / 网络）"; exit 1; }

step "8/8 构建基础镜像 skillsbench-base:latest"
if [ "$SKIP_BUILD" = "1" ]; then
  c_info "已按 --skip-build 跳过基础镜像构建。"
elif docker image inspect skillsbench-base:latest >/dev/null 2>&1 \
     || docker image inspect localhost/skillsbench-base:latest >/dev/null 2>&1; then
  c_ok "基础镜像已存在，跳过构建（如需重建：docker rmi skillsbench-base:latest 后重跑）"
else
  c_info "docker build（podman build；首次较慢，需联网拉 node/npm）..."
  if ( cd "$SKILLSBENCH_DIR" && docker build -f environment/Dockerfile.base -t skillsbench-base:latest environment/ ); then
    c_ok "基础镜像构建完成"
  else
    warn "基础镜像构建失败。最常见是 rootless 容器内 DNS/联网问题——见 docs/server-setup.md 的 F2 排查。"
  fi
fi

# ── 收尾：待人工完成 + 冒烟命令 ──────────────────────────────────────────
step "完成：还需你手动做这几件事"
cat <<'MANUAL'
1) 拷贝 Codex 订阅凭证（rollout + judge 共用，本脚本不碰密钥）：
     从 Mac:  scp ~/.codex/auth.json  <user>@<server>:~/.codex/auth.json
     服务器:  chmod 600 ~/.codex/auth.json
     验证:    codex exec --skip-git-repo-check "reply OK"

2) 准备 tasks_runtime/（被 gitignore，1.6G）：
     用 HuggingFace 数据集 + skillsbench_x/from_flat.py 重建，例如：
     python3 skillsbench_x/from_flat.py --flat-dir ./hf_clone --output-root tasks_runtime --run-id 20260601-group1

3) （可选，仅 claude-code 行）重建 skillsbench/.env：
     printf 'CLAUDE_CODE_OAUTH_TOKEN=<你的token>\n' > skillsbench/.env

4) 让本会话的 PATH / nvm 生效：  source ~/.bashrc   （或重开终端）
MANUAL

step "冒烟测试（按顺序，全绿即迁移成功）"
cat <<'SMOKE'
  cd skillsbench
  docker compose version                                   # compose provider
  docker images | grep skillsbench-base                    # 基础镜像在
  codex exec --skip-git-repo-check "reply OK"              # judge 路径：订阅可用
  uv run bench --help                                      # 依赖装好
  uv run python3 skillsbench_x/run_experiment.py \
    --config experiments/configs/skill-eval/codex-gpt5_5-low.yaml --rows 0 --dry-run
  uv run python3 skillsbench_x/run_experiment.py \
    --config experiments/configs/skill-eval/codex-gpt5_5-low.yaml --rows 0
  ls runs/group1-codex-low-codex-with/1/trajectory.log     # 非空 = ACP 通道 OK
  ls grades/group1-codex-low-codex-with--rev1/1/judge_phase_*.json
SMOKE

if [ "${#WARNINGS[@]}" -gt 0 ]; then
  step "本次运行的告警（${#WARNINGS[@]} 条，请回看处理）"
  for w in "${WARNINGS[@]}"; do c_warn "$w"; done
fi
echo
c_ok "setup-server.sh 结束。详见 docs/server-setup.md。"
