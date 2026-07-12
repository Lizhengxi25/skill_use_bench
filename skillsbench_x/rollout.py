#!/usr/bin/env python3
"""Rollout driver that delegates to BenchFlow's `bench run` per task.

For each task under --tasks:
  1. Build (if needed) the per-task Docker image (a thin layer on top of
     `skillsbench-base:latest`) — BenchFlow does this automatically.
  2. Invoke `uv run bench run <task_dir> --agent <harness> --sandbox docker
     [--skills-dir <task>/environment/skills] --jobs-dir <tmp> --model <model>`.
  3. Locate the rollout output BenchFlow wrote and copy/normalize the
     captured ACP trajectory into runs/<run_id>/<task_id>/trajectory.log.
  4. Write our own metadata.tsv + exit_code.txt so judge.py / aggregate.py
     keep working unchanged.

Re-grade flow: trajectory.log is enough; judge.py reads it back without ever
re-invoking BenchFlow.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HARNESS_AGENT = {
    "claude-code": "claude-agent-acp",
    "codex": "codex-acp",
}

DEFAULT_HARNESS_MODEL = {
    "claude-code": "claude-sonnet-4-6",
    "codex": "gpt-5.5",
}

MINIMAX_CODEX_DEFAULT_REASONING = "high"


def is_minimax_model(model: str) -> bool:
    """Return whether a provider-qualified model selects MiniMax."""
    normalized = model.strip().lower()
    return (normalized.startswith("minimax/")
            or normalized.startswith("openrouter/minimax/"))


def resolve_reasoning_effort(agent: str, model: str,
                             requested: str | None) -> str | None:
    """Resolve the actual reasoning value independently from run labels."""
    if requested is not None:
        return requested
    if agent == "codex-acp" and is_minimax_model(model):
        return MINIMAX_CODEX_DEFAULT_REASONING
    return None


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Size caps per tool-call `kind`, applied to the rendered text per event.
# The codex-acp wrapper currently leaves the ACP `content` field empty for all
# tool_call events (verified against benchflow 7a479e9 + @zed-industries/codex-acp).
# So in practice we render the `title` field — which IS informative for
# `kind=execute` events (full shell command / apply_patch heredoc / python edit
# scripts), but only carries the basename for `read` and the regex pattern for
# `search`.  The caps here size the per-event text after head/tail truncation.
RESULT_CAP_BY_KIND = {
    "execute": 16 * 1024,
    "edit":    16 * 1024,
    "read":     8 * 1024,
    "search":   4 * 1024,
    "list":      512,
    "other":   1 * 1024,
}

# Pinned diagnostic shown once at the top so the judge knows what evidence
# is and is NOT available from a codex-acp trajectory.  Helps the LLM-judge
# avoid penalizing the agent for behaviors that the trajectory format hides.
ACP_TRAJECTORY_PREAMBLE = (
    "# Trajectory format note\n"
    "This trajectory was captured via ACP (Agent-Client Protocol) from a codex-acp\n"
    "wrapper.  Each tool_call event records:\n"
    "  - kind           the tool family (read / search / execute / edit / list / other)\n"
    "  - title          short label OR (for kind=execute/edit) the full command body\n"
    "  - status         completed / failed / cancelled\n"
    "  - content        tool result text -- NOT populated by the current wrapper\n"
    "                   for ANY kind (verified empty for both read and search; for\n"
    "                   execute/edit the script logic appears in `title` instead).\n"
    "Implications for grading:\n"
    "  * For kind=read  : you can see WHICH file was read but not its contents.\n"
    "  * For kind=search: you can see the pattern but not the matched lines.\n"
    "  * For kind=execute/edit: the `title` carries the executed command/script.\n"
    "Score based on what is recorded; do not penalize for missing tool output\n"
    "when the kind is read/search.\n"
)


def _truncate(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    head = text[: cap // 2]
    tail = text[-cap // 2 :]
    return f"{head}\n... [truncated {len(text) - cap} bytes] ...\n{tail}"


def _render_tool_call(ev: dict) -> list[str]:
    """Render a tool_call event into a readable block.

    The ACP content array is observed empty for codex-acp; we surface what's
    available (title is the richest field for execute/edit) and apply a
    per-kind cap so the trajectory stays judge-friendly in token cost.
    """
    kind = ev.get("kind", "other")
    title = ev.get("title", "") or ""
    status = ev.get("status", "")
    call_id = ev.get("tool_call_id", "")
    content = ev.get("content") or []

    cap = RESULT_CAP_BY_KIND.get(kind, RESULT_CAP_BY_KIND["other"])
    body: list[str] = [
        f"kind:    {kind}",
        f"call_id: {call_id}",
        f"status:  {status}",
    ]
    if title:
        body.append(f"title:   {_truncate(title, cap)}")
    if content:
        # ACP content is a list of blocks; flatten to text
        flat = []
        for block in content:
            if isinstance(block, dict):
                for key in ("text", "value", "content"):
                    if key in block and isinstance(block[key], str):
                        flat.append(block[key])
                        break
                else:
                    flat.append(json.dumps(block, ensure_ascii=False))
            else:
                flat.append(str(block))
        joined = "\n".join(flat)
        body.append(f"content: {_truncate(joined, cap)}")
    else:
        body.append("content: (not surfaced by codex-acp wrapper)")
    return body


def jsonl_to_text(jsonl_path: Path) -> str:
    """Render an ACP-style trajectory JSONL into readable text for the judge.

    Strategy:
      - Emit a preamble explaining the trajectory format and its known gaps
        so the judge does not penalize the agent for codex-acp's missing
        tool result content.
      - One block per event, headed `[#<lineno> <type>]`.
      - For tool_call: structured kind/title/status/content with per-kind caps.
      - For user_message / agent_message / agent_thought: full text.
    """
    if not jsonl_path.exists():
        return ""
    out: list[str] = [ACP_TRAJECTORY_PREAMBLE, ""]
    with jsonl_path.open() as f:
        for lineno, line in enumerate(f, start=1):
            line = line.rstrip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                out.append(f"[#{lineno} raw]\n{line}")
                continue
            evtype = ev.get("type") or ev.get("event") or "event"
            header = f"[#{lineno} {evtype}]"
            if evtype == "tool_call":
                body = [header, *_render_tool_call(ev)]
            elif evtype in ("user_message", "agent_message", "agent_thought"):
                text = ev.get("text") or ""
                body = [header, text]
            else:
                # Fallback for unknown event types: compact JSON, capped.
                extra = {k: v for k, v in ev.items() if k != "type"}
                body = [header, _truncate(json.dumps(extra, ensure_ascii=False), 4096)]
            out.append("\n".join(body))
    return "\n\n".join(out) + "\n"


# ──────────────────────────────────────────────────────────────────────────
# Codex native-session renderer
#
# codex-acp's ACP stream is lossy (read/search tool *results* and all reasoning
# are dropped).  BenchFlow harvests codex's own native on-disk session jsonl out
# of the sandbox (trajectory/codex_native_session.jsonl); this renderer turns
# that richer format into the same judge-facing text blocks `jsonl_to_text`
# produces.  Native schema is `{"type": "response_item"|..., "payload": {...}}`.
# ──────────────────────────────────────────────────────────────────────────

CODEX_NATIVE_PREAMBLE = (
    "# Trajectory format note\n"
    "This trajectory was extracted from codex's NATIVE on-disk session jsonl\n"
    "(harvested from the sandbox), NOT the lossy codex-acp ACP stream.  It records\n"
    "the full tool I/O the agent actually saw:\n"
    "  - function_call         a tool invocation: `name` + full arguments (for\n"
    "                          exec_command the shell command; for apply_patch the\n"
    "                          patch body).\n"
    "  - function_call_output  the FULL tool result (file contents, search hits,\n"
    "                          command stdout/stderr), paired to its call by call_id.\n"
    "  - assistant/user_message  agent messages and the user instruction.\n"
    "Reasoning prose is NOT present: codex/GPT encrypts raw chain-of-thought and no\n"
    "readable summary is emitted, so do not expect or require reasoning evidence.\n"
)

# Auto-injected context blocks (not real agent/user content) we skip.
_CODEX_CONTEXT_PREFIXES = (
    "<environment_context",
    "<permissions",
    "<skills_instructions",
    "<plugins_instructions",
    "<user_instructions",
)


def _codex_block_text(content) -> str:
    """Flatten a codex message `content` (list of blocks, or str) to text,
    dropping auto-injected <...> context blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False)
    parts = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        text = block.get("text")
        if not isinstance(text, str):
            text = block.get("content") if isinstance(block.get("content"), str) else None
        if text is None:
            parts.append(json.dumps(block, ensure_ascii=False))
            continue
        if text.lstrip().startswith(_CODEX_CONTEXT_PREFIXES):
            continue
        parts.append(text)
    return "\n".join(p for p in parts if p)


def _render_codex_function_call(payload: dict, cap: int) -> list[str]:
    name = payload.get("name", "") or ""
    call_id = payload.get("call_id", "") or ""
    raw_args = payload.get("arguments", "")
    args = raw_args
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except (json.JSONDecodeError, ValueError):
            args = raw_args
    if isinstance(args, dict):
        cmd = args.get("cmd") or args.get("command")
        if cmd is not None:
            detail = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        elif "input" in args:        # apply_patch
            detail = str(args["input"])
        elif "patch" in args:
            detail = str(args["patch"])
        else:
            detail = json.dumps(args, ensure_ascii=False)
    else:
        detail = str(args)
    return [
        f"name:    {name}",
        f"call_id: {call_id}",
        f"args:    {_truncate(detail, cap)}",
    ]


def _render_codex_function_output(payload: dict, cap: int) -> list[str]:
    call_id = payload.get("call_id", "") or ""
    output = payload.get("output", "")
    text = output if isinstance(output, str) else _codex_block_text(output)
    return [
        f"call_id: {call_id}",
        f"output:  {_truncate(text, cap)}",
    ]


def codex_native_jsonl_to_text(jsonl_path: Path) -> str:
    """Render codex's native session JSONL into readable text for the judge.

    Walks `response_item` entries in order (the authoritative content stream):
    messages (skipping developer/system + injected context), function_call, and
    function_call_output (paired to their call by adjacency + call_id). Skips
    `reasoning` (encrypted) and the duplicate `event_msg` UI stream.
    """
    if not jsonl_path.exists():
        return ""
    out: list[str] = [CODEX_NATIVE_PREAMBLE, ""]
    n = 0
    msg_cap = 16 * 1024
    exec_cap = RESULT_CAP_BY_KIND["execute"]
    with jsonl_path.open() as f:
        for line in f:
            line = line.rstrip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") != "response_item":
                continue
            p = ev.get("payload") or {}
            ptype = p.get("type")
            if ptype == "message":
                role = p.get("role", "?")
                if role == "developer":
                    continue  # system instructions, not agent behavior
                text = _codex_block_text(p.get("content", ""))
                if not text.strip():
                    continue
                n += 1
                out.append(f"[#{n} {role}_message]\n{_truncate(text, msg_cap)}")
            elif ptype == "function_call":
                n += 1
                body = _render_codex_function_call(p, exec_cap)
                out.append("\n".join([f"[#{n} function_call]", *body]))
            elif ptype == "function_call_output":
                n += 1
                body = _render_codex_function_output(p, exec_cap)
                out.append("\n".join([f"[#{n} function_call_output]", *body]))
            # reasoning / other response_items: skip (encrypted / irrelevant)
    return "\n\n".join(out) + "\n"


def discover_tasks(tasks_arg: Path) -> list[Path]:
    if (tasks_arg / "task.toml").exists():
        return [tasks_arg]
    return sorted(
        p for p in tasks_arg.iterdir()
        if p.is_dir() and (p / "task.toml").exists()
    )


def locate_rollout_dir(jobs_dir: Path, task_id: str) -> Path | None:
    """Find the rollout output directory BenchFlow created for this task.

    BenchFlow writes rollouts under jobs_dir/rollout_<sanitized-name>_<idx>/
    or jobs_dir/<task-name>__<trial>/ depending on version.  We scan for any
    subdir containing agent/ + verifier/.
    """
    if not jobs_dir.exists():
        return None
    candidates = []
    for p in jobs_dir.rglob("*"):
        if p.is_dir() and (p / "agent").is_dir() and (p / "verifier").is_dir():
            candidates.append(p)
    if not candidates:
        return None
    # most-recently-modified wins
    return max(candidates, key=lambda p: p.stat().st_mtime)


def run_one(task_dir: Path, run_dir: Path, agent: str, model: str | None,
            with_skills: bool, repo_root: Path,
            bench_extra_args: list[str],
            reasoning_effort: str | None = None,
            prompt_args: list[str] | None = None,
            prompt_note: str = "",
            capture_workspace: bool = False,
            skip_verify: bool = False) -> tuple[str, int, str]:
    task_id = task_dir.name
    out = run_dir / task_id
    out.mkdir(parents=True, exist_ok=True)

    # benchflow bind-mounts dirs under jobs_dir into the container and then
    # `docker compose cp`s files in preserving host ownership. Rootless Podman
    # cannot lchown bind-mounted *NFS* files ("operation not permitted"), and this
    # repo's runs/ lives on NFS (/home). Keep jobs_dir on LOCAL disk ($TMPDIR, e.g.
    # the per-job /tmp on a SLURM node); the harvested artifacts below are copied
    # into `out` (NFS) for persistence. Override the base with SKILLSBENCH_JOBS_ROOT.
    jobs_root = Path(os.environ.get("SKILLSBENCH_JOBS_ROOT") or tempfile.gettempdir())
    jobs_dir = jobs_root / "skillsbench-bfjobs" / run_dir.name / task_id
    if jobs_dir.exists():
        shutil.rmtree(jobs_dir)
    jobs_dir.mkdir(parents=True)

    started_at = utcnow()
    (out / "metadata.tsv").write_text(
        f"task_id\t{task_id}\n"
        f"agent\t{agent}\n"
        f"model\t{model or ''}\n"
        f"reasoning_effort\t{reasoning_effort or ''}\n"
        f"with_skills\t{with_skills}\n"
        f"prompt\t{prompt_note}\n"
        f"capture_workspace\t{capture_workspace}\n"
        f"skip_verify\t{skip_verify}\n"
        f"task_dir\t{task_dir}\n"
        f"jobs_dir\t{jobs_dir}\n"
        f"started_at_utc\t{started_at}\n"
    )

    cmd = [
        "uv", "run", "bench", "run",
        str(task_dir),
        "--agent", agent,
        "--sandbox", "docker",
        "--jobs-dir", str(jobs_dir),
    ]
    if model:
        cmd += ["--model", model]
    if reasoning_effort:
        # Forward to BenchFlow's `--reasoning-effort` flag (added in the
        # feat/reasoning-effort patch).  Validation lives upstream:
        # RolloutConfig.__post_init__ rejects unknown values, and
        # Rollout.setup raises when the chosen agent has no
        # reasoning_effort_flag — so we just pass the string through.
        cmd += ["--reasoning-effort", reasoning_effort]
    if with_skills:
        skills_dir = task_dir / "environment" / "skills"
        if skills_dir.exists():
            cmd += ["--skills-dir", str(skills_dir)]
    cmd += list(prompt_args or [])
    if capture_workspace:
        cmd += ["--capture-workspace"]
    if skip_verify:
        cmd += ["--skip-verify"]
    cmd += bench_extra_args

    bench_log = out / "bench.log"
    with bench_log.open("wb") as lf:
        proc = subprocess.run(cmd, cwd=repo_root, stdout=lf, stderr=subprocess.STDOUT)
    rc = proc.returncode

    rollout_dir = locate_rollout_dir(jobs_dir, task_id)
    trajectory_text = ""
    note = ""
    if rollout_dir is not None:
        # Prefer codex's harvested native session (full tool calls + results)
        # over the lossy codex-acp ACP trajectory.  BenchFlow only harvests this
        # file for codex runs, so its presence is the harness signal.
        native_codex_candidates = [
            rollout_dir / "trajectory" / "codex_native_session.jsonl",
            rollout_dir / "agent" / "codex_native_session.jsonl",
        ]
        # os.access(R_OK): under rootless Podman, files benchflow `docker compose cp`s
        # back into the container's bind-mounted logs land owned by a container subuid
        # (host uid 2649 maps to container 0, so "2649" -> a subuid) and are unreadable
        # by the host harvester. Skip those and fall back to the host-written copy.
        native_codex = next(
            (p for p in native_codex_candidates
             if p.exists() and p.stat().st_size > 0 and os.access(p, os.R_OK)),
            None,
        )
        # ACP harness trajectory (codex-acp / claude-agent-acp / ...)
        traj_jsonl_candidates = [
            rollout_dir / "agent" / "acp_trajectory.jsonl",
            rollout_dir / "trajectory" / "acp_trajectory.jsonl",
            rollout_dir / "agent" / "trajectory.jsonl",
        ]
        traj_jsonl = next((p for p in traj_jsonl_candidates
                           if p.exists() and p.stat().st_size > 0 and os.access(p, os.R_OK)), None)
        transcript = rollout_dir / "agent" / "transcript.txt"
        oracle_txt = rollout_dir / "agent" / "oracle.txt"
        # BenchFlow writes non-protocol stdout and the separately drained stderr
        # beside the ACP agent.  jobs_dir intentionally lives on local scratch,
        # so harvest both logs into the persistent run leaf just like result.json.
        agent_log = rollout_dir / "agent" / f"{agent.replace('-', '_')}.txt"
        agent_stderr_log = agent_log.with_name(
            f"{agent_log.stem}.stderr{agent_log.suffix}"
        )

        if native_codex is not None:
            trajectory_text = codex_native_jsonl_to_text(native_codex)
            note = f"normalized from {native_codex.relative_to(rollout_dir)} (codex native session)"
            shutil.copy2(native_codex, out / "trajectory.jsonl")
        elif traj_jsonl is not None:
            trajectory_text = jsonl_to_text(traj_jsonl)
            note = f"normalized from {traj_jsonl.relative_to(rollout_dir)}"
            shutil.copy2(traj_jsonl, out / "trajectory.jsonl")
        elif transcript.exists():
            trajectory_text = transcript.read_text()
            note = "taken from agent/transcript.txt"
        elif oracle_txt.exists():
            trajectory_text = oracle_txt.read_text()
            note = "taken from agent/oracle.txt (oracle mode)"

        for rel in ("result.json", "agent/transcript.txt", "verifier/reward.txt",
                    "artifacts/workspace.tgz"):
            src = rollout_dir / rel
            if src.exists() and os.access(src, os.R_OK):
                shutil.copy2(src, out / Path(rel).name)
        for src in (agent_log, agent_stderr_log):
            if src.exists() and os.access(src, os.R_OK):
                shutil.copy2(src, out / src.name)

    if not trajectory_text and rc == 0:
        note = "WARNING: rollout completed but no trajectory artifact found"

    (out / "trajectory.log").write_text(trajectory_text)

    ended_at = utcnow()
    with (out / "metadata.tsv").open("a") as mf:
        mf.write(
            f"ended_at_utc\t{ended_at}\n"
            f"exit_code\t{rc}\n"
            f"rollout_dir\t{rollout_dir or ''}\n"
            f"trajectory_note\t{note}\n"
        )
    (out / "exit_code.txt").write_text(f"{rc}\n")

    return task_id, rc, note


def main() -> int:
    parser = argparse.ArgumentParser(description="BenchFlow-driven rollout for skill-eval tasks")
    parser.add_argument("--tasks", required=True, help="tasks/ root or a single tasks/<id>/")
    parser.add_argument("--harness", choices=["codex", "claude-code"], default="codex")
    parser.add_argument("--agent", default=None,
                        help="Override BenchFlow agent name (default chosen from --harness)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning", default=None,
                        help="Reasoning effort (none/minimal/low/medium/high/xhigh). "
                             "Forwarded to `bench run --reasoning-effort`; only "
                             "agents whose AgentConfig declares reasoning_effort_flag "
                             "(currently codex-acp) accept it. MiniMax through Codex "
                             "defaults to high (Adaptive Thinking) when omitted. Typos "
                             "and unsupported agents fail fast at rollout setup.")
    parser.add_argument("--prompt", default=None,
                        help="Text prepended before the task query "
                             "(forwarded to `bench run --prompt-prefix`).")
    parser.add_argument("--prompt-file", default=None,
                        help="File whose contents are prepended before the task query "
                             "(forwarded to `bench run --prompt-file`; wins over --prompt).")
    parser.add_argument("--capture-workspace", action="store_true",
                        help="Snapshot each agent's working dir (/app) to "
                             "runs/<run>/<task>/workspace.tgz before the container is "
                             "destroyed (forwarded to `bench run --capture-workspace`; "
                             "excludes node_modules/.venv/.git/…).")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Skip benchflow's verify phase (no-op test.sh + pre-verify "
                             "hardening) — correct for rollout-only / external-judge runs, "
                             "and avoids the harden `find /` 10s-timeout failure "
                             "(forwarded to `bench run --skip-verify`).")
    parser.add_argument("--with-skills", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--runs-root", default="runs")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--repo-root", default=None,
                        help="cwd for `uv run bench`; defaults to professional/skillsbench/")
    parser.add_argument("bench_extra_args", nargs=argparse.REMAINDER,
                        help="forwarded to `bench run` (use `--` prefix)")
    args = parser.parse_args()

    if shutil.which("uv") is None:
        sys.exit("uv CLI not found on PATH (needed to invoke `uv run bench`)")
    if shutil.which("docker") is None:
        sys.exit("docker CLI not found on PATH")

    tasks_arg = Path(args.tasks).resolve()
    task_dirs = discover_tasks(tasks_arg)
    if not task_dirs:
        sys.exit(f"no tasks found under {tasks_arg}")

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[1]
    if not (repo_root / "pyproject.toml").exists():
        sys.exit(f"--repo-root {repo_root} has no pyproject.toml; pass --repo-root explicitly")

    agent = args.agent or DEFAULT_HARNESS_AGENT[args.harness]
    model = args.model or DEFAULT_HARNESS_MODEL[args.harness]
    reasoning_effort = resolve_reasoning_effort(agent, model, args.reasoning)
    extra = list(args.bench_extra_args or [])
    if extra and extra[0] == "--":
        extra = extra[1:]

    # Pre-query prompt (forwarded to `bench run`; file wins over inline text).
    prompt_args: list[str] = []
    prompt_note = ""
    if args.prompt_file:
        pf = Path(args.prompt_file).resolve()
        if not pf.is_file():
            sys.exit(f"--prompt-file not found: {pf}")
        prompt_args = ["--prompt-file", str(pf)]
        prompt_note = f"file:{pf}"
    elif args.prompt:
        prompt_args = ["--prompt-prefix", args.prompt]
        prompt_note = "inline"

    run_id = args.run_id or (utcnow().replace(":", "").replace("-", "") + f"-{os.getpid()}")
    run_dir = Path(args.runs_root).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "run_id": run_id,
        "harness": args.harness,
        "agent": agent,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "with_skills": args.with_skills,
        "prompt": prompt_note,
        "capture_workspace": args.capture_workspace,
        "skip_verify": args.skip_verify,
        "task_ids": [t.name for t in task_dirs],
        "started_at_utc": utcnow(),
        "bench_extra_args": extra,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"run_dir: {run_dir}")
    print(
        f"agent: {agent}  with_skills: {args.with_skills}  "
        f"reasoning_effort: {reasoning_effort or '-'}  tasks: {len(task_dirs)}"
    )

    failures = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {
            ex.submit(run_one, t, run_dir, agent, model,
                      args.with_skills, repo_root, extra,
                      reasoning_effort, prompt_args, prompt_note,
                      args.capture_workspace, args.skip_verify): t.name
            for t in task_dirs
        }
        for fut in as_completed(futs):
            # One task's unexpected exception must not abort the whole batch —
            # log it as a failure and keep rolling out the remaining tasks.
            try:
                task_id, rc, note = fut.result()
            except Exception as e:  # noqa: BLE001
                task_id, rc, note = futs[fut], 1, f"EXCEPTION: {e!r}"
            tag = "OK" if rc == 0 else f"FAIL:{rc}"
            print(f"[{tag}] {task_id}" + (f"  ({note})" if note else ""))
            if rc != 0:
                failures += 1

    manifest["ended_at_utc"] = utcnow()
    manifest["failures"] = failures
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
