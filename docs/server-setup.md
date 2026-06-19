# 在服务器上部署 SkillsBench 实验（无 sudo / rootless Podman）

把 SkillsBench 的 `skillsbench_x/` 实验流程（**rollout + LLM-as-judge**）从 MacBook 迁移到一台
**Ubuntu 24.04** 服务器的完整手册。目标服务器的两条硬约束贯穿全文：

- **无 sudo 权限** —— 一切走用户级安装，不碰系统目录。
- **容器运行时是 rootless Podman** —— `docker` 命令其实是 `podman-docker` 壳。能跑容器，无需 sudo。

> 一键脚本：仓库根目录的 [`setup-server.sh`](../setup-server.sh) 自动完成下面 C 节的环境安装。
> 本文是它的说明书 + 排查手册 + 订阅配置 + 冒烟验证。

---

## 0. 实验流程速览

```
run_experiment.py  读 YAML 配置，对 matrix 每行依次跑三段：

  ┌─ rollout ──────────────────────────────────────────────┐
  │ rollout.py → `uv run bench run <task> --agent codex-acp │
  │              --sandbox docker [--model gpt-5.5]         │
  │              [--reasoning-effort low] [--skills-dir …]` │
  │ agent(codex-acp) 跑在容器里(skillsbench-base:latest)；   │
  │ benchflow 把宿主机 ~/.codex/auth.json 上传进容器；        │
  │ 任务 allow_internet=false → 容器 network_mode:none，     │
  │ agent ↔ benchflow 走 `docker compose exec -i` stdio。   │
  └────────────────────────────────────────────────────────┘
  ┌─ judge ────────────────────────────────────────────────┐
  │ judge.py 在【宿主机】直接 `codex exec --ephemeral        │
  │   --sandbox read-only -c model_reasoning_effort=<eff>   │
  │   --output-last-message out.json <rubric>`              │
  │ 判分模型 = 宿主机 ~/.codex/config.toml 的默认 model。     │
  └────────────────────────────────────────────────────────┘
  ┌─ aggregate ─ aggregate.py → analyze_grades.py 级联打分 ─┐
  └────────────────────────────────────────────────────────┘
```

**关键认知：rollout 用容器里的 `codex-acp`，judge 用宿主机的 `codex` CLI——两套 codex，
共用同一份 `~/.codex/auth.json`（ChatGPT 订阅）。**

---

## A. 在服务器上必须额外补齐的东西（git 之外）

clone 两个仓库还不够，以下都被 gitignore 或属于本地状态，需要单独准备：

| # | 缺口 | 怎么补 |
|---|------|--------|
| 1 | `skillsbench` 无 git remote | 自建 GitHub remote 或自行拷贝 |
| 2 | `benchflow` 是本地 path 依赖（`../benchflow`，editable），分支 `feat/reasoning-effort` | 把 benchflow 作为 skillsbench 的**同级目录**，`git checkout feat/reasoning-effort` |
| 3 | `tasks_runtime/`（1.6G，gitignore） | HuggingFace 数据集 + `skillsbench_x/from_flat.py` 重建 |
| 4 | `skillsbench/.env`（含 `CLAUDE_CODE_OAUTH_TOKEN`，仅 claude-code 行用） | 服务器重建（可选） |
| 5 | 订阅凭证 `~/.codex/auth.json` + `config.toml` 的 `model` | 见 [D 节](#d-subscription-非-api-key-鉴权) |
| 6 | 基础镜像 `skillsbench-base:latest` | 服务器 `docker build`（podman build） |
| 7 | dev 依赖 `skills-ref`（git 拉取） | 跑实验不需要，`uv sync --no-dev` 跳过 |
| 8 | Podman 没有 `docker compose` provider | 见 [C1](#c1-容器运行时只缺-compose-provider) |

> benchflow remote：`git@github.com:Lizhengxi25/benchflow_reasoning_efforts_patch.git`

依赖关系（`skillsbench/pyproject.toml`）：
```toml
[tool.uv.sources]
benchflow = { path = "../benchflow", editable = true }
```
所以服务器上目录布局必须是：
```
<parent>/
  benchflow/      # 分支 feat/reasoning-effort
  skillsbench/    # 与 benchflow 同级
```

---

## B. 敏感信息审计结论（git 已跟踪文件）

**两个仓库都没有提交任何真实密钥 / 令牌 / 凭证，可安全传输或公开。**

- ✅ 没提交真实 `.env`（只有 `benchflow/.env.sample` 模板占位符）。
- ✅ 没有绝对家目录路径泄漏、没有 `.credentials.json` / 私钥 / service-account。
- ⚠️ 唯一的「个人信息」是上游包元数据里的公开作者邮箱（`benchflow/pyproject.toml`、`CITATION.cff`：
  `xiangyi@benchflow.ai`、`choe.kyoung@gmail.com`）——开源署名，非密钥，保留即可。
- ⚠️ `tasks_excluded/*/task.toml` 的 `MODAL_TOKEN_ID = "${MODAL_TOKEN_ID}"` 是 shell 占位语法，非真值。

**真正的敏感物**（`~/.codex/auth.json`、`~/.codex/config.toml`、`skillsbench/.env` 里的 token）
**本来就没被 git 跟踪**——迁移时务必别手滑 `git add` / push。

---

## C. 服务器环境配置（无 sudo）

> 直接跑 `bash setup-server.sh` 即可自动完成 C1–C3；下面是逐步说明与原理，便于排查。

### C0. 预检容器运行时
```bash
docker info        # 应正常输出（这里其实是 podman）；失败则 rootless Podman 未就绪
docker ps          # 应能列出（空也行）
```

### C1. 容器运行时：只缺 compose provider ★关键
`docker` = podman 壳已可用。但 benchflow 硬编码调用 `docker compose …`（compose v2 子命令），
Podman 默认没有 compose 提供方，必须补一个。

**方案 P（首选，只装 1 个二进制）：**
```bash
mkdir -p ~/.local/bin
curl -fsSL -o ~/.local/bin/docker-compose \
  https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64
chmod +x ~/.local/bin/docker-compose
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && export PATH="$HOME/.local/bin:$PATH"

mkdir -p ~/.config/containers
cat >> ~/.config/containers/containers.conf <<'EOF'
[engine]
compose_providers = ["docker-compose"]
compose_warning_logs = false
EOF

docker compose version    # 能打印 Docker Compose v2.x 即 OK
```
原理：podman 壳把 `docker compose …` 转成 `podman compose …`，podman 再按 `compose_providers`
调用上面的 `docker-compose` v2 二进制（并自动把 `DOCKER_HOST` 指向自己的 socket）。

**方案 D（备用，当方案 P 的 compose 流式/参数转换出问题时）：** 直接让一个**真 docker CLI** 连
Podman 的 Docker 兼容 socket：
```bash
# 装真 docker CLI 静态二进制 + compose 插件到用户目录（无 sudo），再：
export DOCKER_HOST="unix:///run/user/$(id -u)/podman/podman.sock"
echo "export DOCKER_HOST=\"unix:///run/user/\$(id -u)/podman/podman.sock\"" >> ~/.bashrc
```
benchflow 自身已支持读取 `DOCKER_HOST`（`sandbox/process.py` / `_host_env`），无需改源码。

### C2. 宿主机工具（judge / 包管理，全用户级）
```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh
# Node（nvm）
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
. ~/.nvm/nvm.sh && nvm install 22
# 宿主机 codex CLI —— judge 必需（judge 在宿主机直接 `codex exec`）
npm install -g @openai/codex        # 对齐 Mac 的 codex-cli 0.134.0
# 可选：仅 claude-code 行
npm install -g @anthropic-ai/claude-code
```
- Python 不必手装：`uv` 按 `.python-version`(3.12) 自建虚拟环境。
- **judge 沙箱**：Linux 上 `codex exec --sandbox read-only` 走 Landlock/seccomp（Mac 是 Seatbelt）。
  内核 6.12 一般 OK；若报沙箱错，judge 可降级 `--sandbox danger-full-access`（只读 trajectory + 调 API，安全）。

### C3. 装依赖 + 建基础镜像
```bash
cd skillsbench
uv sync --no-dev      # benchflow 同级且在 feat/reasoning-effort；--no-dev 跳过 skills-ref
docker build -f environment/Dockerfile.base -t skillsbench-base:latest environment/
```

---

## D. Subscription（非 API key）鉴权

### D1. Codex（gpt-5.5）—— rollout + judge 共用一份凭证
Mac 上 `~/.codex/auth.json` 的 `OPENAI_API_KEY=null`、含 `tokens`(OAuth)+`last_refresh`，即
**ChatGPT 订阅登录**；`config.toml` 顶部 `model = "gpt-5.5"`。服务器（无图形界面）做法：

```bash
mkdir -p ~/.codex && chmod 700 ~/.codex
# 从 Mac 拷过来：
#   scp ~/.codex/auth.json  <user>@<server>:~/.codex/auth.json
chmod 600 ~/.codex/auth.json

# config.toml 不必整份拷（Mac 那份很大，全是状态垃圾），只要 model 正确：
grep -qE '^\s*model\s*=' ~/.codex/config.toml 2>/dev/null \
  || printf 'model = "gpt-5.5"\n' >> ~/.codex/config.toml   # 注意：若文件已有 [table]，需把该行放到文件顶部

codex exec --skip-git-repo-check "reply OK"    # 验证订阅可用
```
- OAuth token 会自动刷新，拷过去时有效即可。
- **rollout 无需额外操作**：benchflow 的 `SubscriptionAuth` 探测 `~/.codex/auth.json` 并逐 task
  上传进容器（`registry.py` 的 codex-acp 配置），codex-acp 直接用。所以宿主机配好这一份，
  judge（宿主机）+ rollout（容器）全覆盖。
- ⚠️ **关键坑：环境里不能有 `OPENAI_API_KEY`。** 一旦 shell 里 `export OPENAI_API_KEY=...`（本机
  在 `~/.bashrc` 里有），benchflow 会判定为 **API-key 鉴权**：它用 `{"OPENAI_API_KEY": "..."}`
  **覆盖**掉容器里的 `~/.codex/auth.json`（而不是上传 OAuth 订阅 tokens）。codex-acp 于是拿这个
  key 走 API 调 gpt-5.5，容器内模型调用直接失败 → `ACP error -32603: Internal error`（rollout
  能连上 ACP、能 set_model，但一发 prompt 就挂）。判分（宿主机 `codex exec`）不受影响——它认
  `auth.json` 的 OAuth，env 里的 key 会被忽略。
  - 修复：跑 rollout 前 `unset OPENAI_API_KEY`。`slurm-podman-bootstrap.sh` 已在每个作业开头
    `unset OPENAI_API_KEY BENCHFLOW_PROVIDER_API_KEY`；**登录节点交互式直接跑** rollout/run_experiment
    时也要 unset（或把 `~/.bashrc` 里那行 `export OPENAI_API_KEY` 注释掉——本实验全程用订阅、不用
    API key）。
- 备选：服务器 `codex login` + 笔记本侧 `ssh -L 1455:localhost:1455 <user>@<server>`，浏览器在
  笔记本完成 OAuth。

### D2. Claude（可选，仅 claude-code 行）
Mac 上 `~/.claude/.credentials.json` 不存在（macOS 用 Keychain），claude 订阅靠 `skillsbench/.env`
的 `CLAUDE_CODE_OAUTH_TOKEN`（benchflow 读取并注入容器）。服务器：
```bash
printf 'CLAUDE_CODE_OAUTH_TOKEN=<你的token>\n' > skillsbench/.env
# 或在服务器用 claude CLI 重新生成长期 token：
claude setup-token
```
当前实验 config 里 claude 行是注释掉的，codex 实验不需要这步。

---

## E. 准备 tasks_runtime/

`tasks_runtime/` 被 gitignore，需从 HuggingFace 数据集用 `from_flat.py` 重建。每套题一个 run-id：
```bash
cd skillsbench
python3 skillsbench_x/from_flat.py --flat-dir ./hf_clone --output-root tasks_runtime --run-id 20260601-group1
python3 skillsbench_x/from_flat.py --flat-dir ./hf_clone --output-root tasks_runtime --run-id 20260602-group2
```
每个 task 目录含 `task.toml` / `instruction.md` / `environment/`（`Dockerfile` 是 `FROM
skillsbench-base:latest`）/ `rubric/judge_phase_*.md` / `tests/`。实验 config 用
`tasks_root: tasks_runtime/20260601-group1` 指向它。

---

## F. Podman 专项风险 & 冒烟测试（迁移成败就看这几点）

按顺序逐项验证，每条都是潜在断点：

| # | 测试 | 通过标准 / 失败排查 |
|---|------|---------------------|
| F1 | `docker compose version` | 能打印 v2.x。失败 → benchflow 所有 compose 调用会挂；改用 [方案 D](#c1-容器运行时只缺-compose-provider) |
| F2 | `docker build … skillsbench-base:latest` | 构建成功（exercises rootless 网络/DNS：apt+curl nodejs.org+npm）。容器内 DNS 失败 → `~/.config/containers/containers.conf` 加 `[network]\n  dns_servers=["8.8.8.8"]`，或 build 加 `--dns 8.8.8.8` |
| F3 | 单题 `bench run` 时 `FROM skillsbench-base:latest` 命中本地镜像 | 报 shortname/registry 错 → `~/.config/containers/registries.conf` 配 `unqualified-search-registries`，或改用 `localhost/skillsbench-base:latest` 标签 |
| F4 | **`docker compose exec -i` 流式（ACP 通道）** | 单题单行真跑能产出**非空** `trajectory.log`。这条最关键，验证 agent↔benchflow 的 stdio 双向流在 podman 下可用 |
| F5 | compose `deploy.resources.limits`(cpus/memory) | rootless 下生效（cgroup v2 已委派 cpu+memory，`docker info` 已确认，正常 OK） |

### F6. 登录节点提醒（运维向，务必看）
实测环境是 `ciai-login-1`（**登录节点**）。重负载 rollout（大并发 + 镜像构建）跑在登录节点可能违反
集群策略；且 `/scratch`（Podman 镜像/容器存储所在）常被定期清理，**基础镜像可能被清掉，需重建**。
若集群要求在计算节点跑，需先确认计算节点上 rootless Podman + 用户 socket
（`/run/user/$(id -u)/podman/podman.sock`）同样可用。→ 建议与集群管理确认。

### F7. 在 SLURM 计算节点跑 rollout（已验证可行，2026-05）
计算节点（如 `cscc-cpu-p` 的 `cn-*`）**无 systemd 用户会话**，与登录节点有三处关键差异，必须在
作业开头补齐，否则 `sbatch` 提交的 rollout 会失败：

1. **没有 `/run/user/$(id -u)`**（`XDG_RUNTIME_DIR`）→ podman 起不来
   （`Failed to obtain podman configuration: lstat /run/user/<uid>`）。
   对策：在作业里把 `XDG_RUNTIME_DIR` 指到节点本地 `$TMPDIR`，并手动起一个 `podman system service`
   作为 compose provider 的 socket（`DOCKER_HOST`）。
2. **镜像存储是节点本地的**（系统 `storage.conf` 的 `rootless_storage_path=/scratch/$USER/...`，
   而 `/scratch` 每节点独立）→ 登录节点构建的 `skillsbench-base` 在计算节点上不存在；NFS 家目录又
   不支持 overlay 的 xattr（`lsetxattr ... operation not supported`）。
   对策：登录节点 `podman save` 成 tar 放到 NFS 家目录，作业里 `podman load` 进**节点本地** store
   （用 `rootless_storage_path` 覆盖，**不是** `graphroot`——rootless 会忽略 `graphroot`）。
3. **podman 往 stderr 打噪声**：podman-docker 壳的 `Emulate Docker CLI using podman` 横幅 +
   无 systemd 会话时的 `Falling back to cgroupfs` 警告。benchflow 用 `stderr=STDOUT` 合并捕获
   `pwd`（`rollout.py` 里 `agent_cwd`），噪声会污染容器工作目录 → ACP exec 的 `-w` 拿到垃圾 →
   `crun: /app: command not found`。
   对策（一次性、用户级、NFS 共享 `~/.config` 全节点生效）：
   - `touch ~/.config/containers/nodocker`（静音横幅，`/usr/bin/docker` 壳会检查此标记文件）。
   - `~/.config/containers/containers.conf` 的 `[engine]` 加 `cgroup_manager = "cgroupfs"`（静音警告）。
   这两步已并入 `setup-server.sh`。
4. **`_benchflow_jobs`（bind-mount 源）必须在本地盘，不能在 NFS。** rollout 收尾时 benchflow 会
   `docker compose cp` 把 trajectory 拷进容器的 `/logs/agent/`（这是个 bind-mount，源就是
   `_benchflow_jobs/.../agent/`）。podman cp 会保留宿主 uid 去 `lchown`，而**rootless podman 没法
   对 bind-mount 的 NFS 文件 lchown** → `operation not permitted` → rollout 末尾报错（agent 其实已
   跑完）。本仓库 `runs/` 在 NFS `/home`，所以原来 `jobs_dir = runs/<id>/<task>/_benchflow_jobs`
   会触发。Mac 上 `runs/` 在本地盘故无此问题。
   - 修复：`skillsbench_x/rollout.py` 已改成把 `jobs_dir` 放到本地 `$TMPDIR`
     （`tempfile.gettempdir()`，可用 `SKILLSBENCH_JOBS_ROOT` 覆盖）；最终产物（`trajectory.log` /
     `result.json` / `trajectory.jsonl` 等）照旧拷回 `runs/<id>/<task>/`（NFS 持久化）。
     `run_experiment.py` 走同一 `rollout.py`，自动受益。
5. **`--sandbox-user none`**（次要）：实验 config 的 `bench_extra_args` 已带，`try.sh` 也补了
   `-- --sandbox-user none`（`rollout.py` 用 `argparse.REMAINDER`，额外 bench 参数要放 `--` 之后）。
   与上面的 cp 报错无关，但保持与正式实验一致。

落地：以上 1–2 由仓库根目录的 [`slurm-podman-bootstrap.sh`](../../slurm-podman-bootstrap.sh) 自动完成
（被 `try.sh` / `experiments/sweep.slurm` 在作业开头 `source`）。一次性准备：
```bash
# 登录节点：把基础镜像存成 tar 放到 NFS（每次改 Dockerfile.base 后重做）
podman save -o "$HOME/skillsbench-base.tar" localhost/skillsbench-base:latest
```
冒烟（计算节点）：先 `sbatch probe-compute-node.sh` 跑可行性探测（podman/compose/出网/基础镜像/
compose build 六项全 PASS），再 `sbatch try.sh` 真跑单题。验收看 `runs/<id>/7/trajectory.log` 非空。

> 计算节点 `cn-02` 实测：rootless podman + fuse-overlayfs + slirp4netns 出网均可用，宿主机与容器
> 都能到达模型 API；`/scratch`≈`/tmp` 同一本地 XFS（~300G 余量）。pasta 缺失不影响（用 slirp4netns）。

---

## G. 端到端验证

```bash
cd skillsbench
docker compose version                                  # (1) compose provider OK
docker images | grep skillsbench-base                   # (2) 基础镜像在（podman store）
codex exec --skip-git-repo-check "reply OK"             # (3) judge 路径：订阅可用
uv run bench --help                                     # (4) 依赖装好

# (5) 先 dry-run 看命令
uv run python3 skillsbench_x/run_experiment.py \
  --config experiments/configs/skill-eval/codex-gpt5_5-low.yaml --rows 0 --dry-run

# (6) 真跑一行一题（rollout + judge + aggregate）
uv run python3 skillsbench_x/run_experiment.py \
  --config experiments/configs/skill-eval/codex-gpt5_5-low.yaml --rows 0

# 看产物：
ls runs/group1-codex-low-codex-with/1/trajectory.log               # 非空 = ACP 通道 OK
ls grades/group1-codex-low-codex-with--rev1/1/judge_phase_*.json   # judge 产出
```

(6) 全绿即迁移成功。之后跑全量：
```bash
bash experiments/sweep-codex-gpt5_5-efforts.sh
```

---

## 速查：服务器落地总顺序

1. 把 `skillsbench` + `benchflow`（同级、benchflow 在 `feat/reasoning-effort`）弄到服务器。
2. `bash skillsbench/setup-server.sh`（装环境 + compose provider + 建镜像）。
3. 拷 `~/.codex/auth.json`（D1）+ 确认 `config.toml` 的 `model = "gpt-5.5"`。
4. 准备 `tasks_runtime/`（HF → `from_flat.py`，见 E 节）。
5. 按 F 节冒烟、G 节验证 → 全绿后跑全量 sweep。
