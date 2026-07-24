#!/usr/bin/env python3
"""Run the bounded OpenRouter x harness compatibility matrix.

The matrix is intentionally fixed to previously successful lightweight tasks.
Each model/harness/task cell runs both SkillsBench arms. Stage two runs only
after both harnesses passed stage one for that model; GLM-5.2 stops after stage
one. ``--dry-run`` prints every possible command and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SKILLSBENCH_ROOT = Path(__file__).resolve().parents[1]
PROFESSIONAL_ROOT = SKILLSBENCH_ROOT.parent
ROLLOUT_SCRIPT = SKILLSBENCH_ROOT / "skillsbench_x" / "rollout.py"
PROMPT_ROOT = SKILLSBENCH_ROOT / "experiments" / "configs" / "search-env"
DEFAULT_OUTPUT_ROOT = SKILLSBENCH_ROOT / "runs" / "openrouter_harness_smoke"
WITH_SKILL_PROMPTS = {
    "codex": PROMPT_ROOT / "prompt-withskill-codex.md",
    "claude-code": PROMPT_ROOT / "prompt-withskill-claude-code.md",
    "openhands": PROMPT_ROOT / "prompt-withskill.md",
}

TASKS = {
    "rails-dev": (PROFESSIONAL_ROOT / "synthesized_data" / "jun_27_1000_g1_100" / "rails-dev"),
    "rust-router": (PROFESSIONAL_ROOT / "synthesized_data" / "jun_23_syn_100_b" / "rust-router"),
}
MODELS = {
    "hy3": "openrouter/tencent/hy3",
    "gpt-oss-120b": "openrouter/openai/gpt-oss-120b",
    "qwen3.5-397b": "openrouter/qwen/qwen3.5-397b-a17b",
    "glm-5.1": "openrouter/z-ai/glm-5.1",
    "glm-5.2": "openrouter/z-ai/glm-5.2",
    "kimi-k2.6": "openrouter/moonshotai/kimi-k2.6",
    "minimax-m3": "openrouter/minimax/minimax-m3",
    "minimax-m2.7": "openrouter/minimax/minimax-m2.7",
    "deepseek-v4-pro": "openrouter/deepseek/deepseek-v4-pro",
    "deepseek-v4-flash": "openrouter/deepseek/deepseek-v4-flash",
}
HARNESSES = ("claude-code", "openhands")
ARMS = ("no_skill", "with_skill")


@dataclass(frozen=True)
class ArmSpec:
    model_name: str
    model: str
    harness: str
    task_name: str
    task_dir: Path
    arm: str
    run_id: str
    run_leaf: Path
    log_path: Path
    command: tuple[str, ...]


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _default_experiment_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _safe_id(raw: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")
    if not value:
        raise ValueError("identifier cannot be empty")
    return value


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--experiment-id", default=None)
    parser.add_argument(
        "--models",
        default=",".join(MODELS),
        help=f"comma-separated model aliases: {','.join(MODELS)}",
    )
    parser.add_argument(
        "--harnesses",
        default=",".join(HARNESSES),
        help=f"comma-separated harnesses: {','.join(HARNESSES)}",
    )
    parser.add_argument(
        "--arms",
        default=",".join(ARMS),
        help=f"comma-separated arms: {','.join(ARMS)}",
    )
    parser.add_argument("--model-parallelism", type=int, default=2)
    parser.add_argument("--timeout-sec", type=int, default=7200)
    stage = parser.add_mutually_exclusive_group()
    stage.add_argument("--stage1-only", action="store_true")
    stage.add_argument(
        "--stage2-only",
        action="store_true",
        help="run only rust-router after stage one was validated separately",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _selected_models(raw: str) -> list[tuple[str, str]]:
    names = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(names) - set(MODELS))
    if unknown:
        raise ValueError(f"unknown model aliases: {', '.join(unknown)}")
    if not names:
        raise ValueError("--models selected no models")
    return [(name, MODELS[name]) for name in names]


def _selected_values(
    raw: str,
    *,
    allowed: Sequence[str],
    option: str,
) -> list[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise ValueError(f"unknown {option}: {', '.join(unknown)}")
    if not values:
        raise ValueError(f"--{option} selected no values")
    return values


def _validate_inputs() -> None:
    required = [
        ROLLOUT_SCRIPT,
        PROMPT_ROOT / "prompt-noskill.md",
        *WITH_SKILL_PROMPTS.values(),
    ]
    for task_dir in TASKS.values():
        required.extend(
            [
                task_dir,
                task_dir / "task.toml",
                task_dir / "instruction.md",
                task_dir / "environment" / "skills",
            ]
        )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ValueError("required inputs are missing:\n  " + "\n  ".join(missing))


def _run_id(
    model_name: str,
    harness: str,
    task_name: str,
    arm: str,
) -> str:
    return _safe_id(f"{model_name}--{harness}--{task_name}--{arm}")


def build_arm_spec(
    *,
    experiment_dir: Path,
    model_name: str,
    model: str,
    harness: str,
    task_name: str,
    arm: str,
) -> ArmSpec:
    run_id = _run_id(model_name, harness, task_name, arm)
    runs_root = experiment_dir / "rollouts"
    task_dir = TASKS[task_name]
    prompt_file = WITH_SKILL_PROMPTS[harness] if arm == "with_skill" else PROMPT_ROOT / "prompt-noskill.md"
    command = [
        "uv",
        "run",
        "python3",
        "skillsbench_x/rollout.py",
        "--tasks",
        str(task_dir),
        "--harness",
        harness,
        "--model",
        model,
        "--prompt-file",
        str(prompt_file),
        "--run-id",
        run_id,
        "--runs-root",
        str(runs_root),
        "--capture-workspace",
        "--capture-model-io",
        "--concurrency",
        "1",
    ]
    if arm == "with_skill":
        command.append("--with-skills")
    command += ["--", "--sandbox-user", "none"]
    return ArmSpec(
        model_name=model_name,
        model=model,
        harness=harness,
        task_name=task_name,
        task_dir=task_dir,
        arm=arm,
        run_id=run_id,
        run_leaf=runs_root / run_id / task_dir.name,
        log_path=(experiment_dir / "launcher_logs" / f"{model_name}--{harness}--{task_name}--{arm}.log"),
        command=tuple(command),
    )


def _reasoning_control_fields(payload: object) -> list[str]:
    """Return request-level reasoning controls, excluding conversation history.

    Later turns can legitimately replay model output such as
    ``messages[*].thinking_blocks[*].thinking``. Those fields are content, not
    API controls. Final provider controls live at the request root (or in the
    client-only ``extra_body`` wrapper), so inspecting arbitrary nested message
    content creates false positives.
    """
    if not isinstance(payload, dict):
        return []

    found: list[str] = []

    def inspect_request_controls(candidate: object, *, prefix: str = "") -> None:
        if not isinstance(candidate, dict):
            return
        for key in ("reasoning", "reasoning_effort", "include_reasoning", "thinking"):
            if key in candidate:
                found.append(f"{prefix}{key}")
        include = candidate.get("include")
        if isinstance(include, list) and any(isinstance(item, str) and item.startswith("reasoning.") for item in include):
            found.append(f"{prefix}include")
        output_config = candidate.get("output_config")
        if isinstance(output_config, dict) and "effort" in output_config:
            found.append(f"{prefix}output_config.effort")

    inspect_request_controls(payload)
    if "extra_body" in payload:
        inspect_request_controls(payload["extra_body"], prefix="extra_body.")
    return found


def _has_completed_skill_read(
    wire_files: Sequence[Path],
    *,
    task_name: str,
) -> bool:
    """Return whether ACP proves that this task's SKILL.md was read successfully."""
    path_fragment = f"/{task_name}/SKILL.md"
    candidate_ids: set[str] = set()
    completed_ids: set[str] = set()

    for wire_path in wire_files:
        try:
            lines = wire_path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("direction") != "agent_to_client":
                continue
            message = record.get("message")
            if not isinstance(message, dict) or message.get("method") != "session/update":
                continue
            params = message.get("params")
            update = params.get("update") if isinstance(params, dict) else None
            if not isinstance(update, dict):
                continue
            tool_call_id = update.get("toolCallId")
            if not isinstance(tool_call_id, str):
                continue
            raw_input = update.get("rawInput")
            if raw_input is not None:
                serialized_input = json.dumps(raw_input, ensure_ascii=False)
                if path_fragment in serialized_input:
                    candidate_ids.add(tool_call_id)
            if update.get("status") == "completed":
                completed_ids.add(tool_call_id)

    return bool(candidate_ids & completed_ids)


def _validate_litellm_callbacks(
    callback_path: Path,
    *,
    expected_model: str,
) -> list[str]:
    """Reject hidden proxy failures or calls routed to another model."""
    errors: list[str] = []
    expected_provider_model = expected_model.removeprefix("openrouter/")
    accepted_callback_models = {expected_provider_model}
    if expected_provider_model.startswith("openai/"):
        # LiteLLM's Anthropic adapter drops this provider prefix in callbacks.
        # The captured OpenRouter response is checked separately below.
        accepted_callback_models.add(expected_provider_model.removeprefix("openai/"))
    try:
        lines = callback_path.read_text(errors="replace").splitlines()
    except OSError as exc:
        return [f"unreadable LiteLLM callback log: {exc}"]

    saw_record = False
    saw_expected_success = False
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        saw_record = True
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"callback line {line_number} is invalid JSON: {exc}")
            continue
        if not isinstance(record, dict):
            errors.append(f"callback line {line_number} is not a JSON object")
            continue
        event = record.get("event")
        if "event" not in record:
            errors.append(f"callback line {line_number} is missing event")
            continue
        if event not in {"success", "failure"}:
            errors.append(f"callback line {line_number} has unknown event {event!r}")
            continue
        request_model = record.get("request_model")
        provider_model = record.get("provider_model")
        if event == "failure":
            detail = record.get("error")
            error_type = detail.get("type") if isinstance(detail, dict) else None
            errors.append(f"callback line {line_number} failed ({error_type or 'unknown error'}) for model {request_model!r}")
            continue
        if request_model not in accepted_callback_models or provider_model not in accepted_callback_models:
            errors.append(
                f"callback line {line_number} used unexpected model {request_model!r}/{provider_model!r}; expected {expected_provider_model!r}"
            )
            continue
        saw_expected_success = True

    if not saw_record:
        return ["callback log is empty"]
    if not saw_expected_success:
        errors.append(f"callback log has no successful call for expected model {expected_provider_model!r}")
    return errors


def _captured_provider_models(response_path: Path) -> set[str]:
    """Extract canonical model IDs from complete JSON or streamed SSE bytes."""
    text = response_path.read_text(errors="replace")
    payloads: list[object] = []
    try:
        payloads.append(json.loads(text))
    except json.JSONDecodeError:
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if not data or data == "[DONE]":
                continue
            try:
                payloads.append(json.loads(data))
            except json.JSONDecodeError:
                continue

    models: set[str] = set()

    def collect(payload: object) -> None:
        if isinstance(payload, list):
            for item in payload:
                collect(item)
            return
        if not isinstance(payload, dict):
            return
        model = payload.get("model")
        if isinstance(model, str):
            models.add(model)
        # Cover OpenRouter chat chunks, Anthropic message_start, and Responses
        # events without searching arbitrary generated content for "model".
        for key in ("message", "response"):
            collect(payload.get(key))

    for payload in payloads:
        collect(payload)
    return models


def validate_arm(spec: ArmSpec, return_code: int) -> tuple[bool, list[str]]:
    errors: list[str] = []
    leaf = spec.run_leaf
    native_skill_invocations = 0
    if return_code != 0:
        errors.append(f"launcher exit code {return_code}")
    if not leaf.is_dir():
        return False, [*errors, f"missing run leaf {leaf}"]

    exit_code = leaf / "exit_code.txt"
    if not exit_code.is_file() or exit_code.read_text().strip() != "0":
        errors.append("exit_code.txt is missing or non-zero")

    result_path = leaf / "result.json"
    if not result_path.is_file():
        errors.append("missing result.json")
    else:
        try:
            result = json.loads(result_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid result.json: {exc}")
        else:
            if result.get("error") is not None:
                errors.append(f"result.error={result.get('error')!r}")
            if result.get("partial_trajectory") is not False:
                errors.append(f"partial_trajectory={result.get('partial_trajectory')!r}")
            if result.get("capture_model_io") is not True:
                errors.append(f"result.capture_model_io={result.get('capture_model_io')!r}")
            value = result.get("n_skill_invocations")
            if isinstance(value, int):
                native_skill_invocations = value

    config_path = leaf / "config.json"
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid config.json: {exc}")
        else:
            if config.get("capture_model_io") is not True:
                errors.append(f"config.capture_model_io={config.get('capture_model_io')!r}")

    for filename in (
        "trajectory.jsonl",
        "trajectory.log",
        "llm_trajectory.jsonl",
        "config.json",
        "timing.json",
        "prompts.json",
        "workspace.tgz",
    ):
        path = leaf / filename
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing or empty {filename}")

    agent_dir = leaf / "agent"
    wire_files = list(agent_dir.glob("*.acp_wire.jsonl")) if agent_dir.is_dir() else []
    if not wire_files or any(path.stat().st_size == 0 for path in wire_files):
        errors.append("missing or empty ACP wire log")
    completed_skill_read = _has_completed_skill_read(
        wire_files,
        task_name=spec.task_name,
    )
    used_task_skill = native_skill_invocations > 0 or completed_skill_read
    if spec.arm == "with_skill" and not used_task_skill:
        errors.append("with_skill arm has no completed task SKILL.md read")
    if spec.arm == "no_skill" and used_task_skill:
        errors.append("no_skill arm accessed the task skill")
    litellm_root = agent_dir / "litellm"
    litellm_dirs = sorted(path for path in litellm_root.iterdir() if path.is_dir()) if litellm_root.is_dir() else []
    if not litellm_dirs:
        errors.append("missing LiteLLM debug logs")
    recovered_token_count_statuses: set[int] = set()
    for debug_dir in litellm_dirs:
        for filename in ("stdout.log", "stderr.log", "callback.jsonl"):
            if not (debug_dir / filename).is_file():
                errors.append(f"{debug_dir.name}: missing LiteLLM {filename}")
        callback_path = debug_dir / "callback.jsonl"
        if callback_path.is_file():
            errors.extend(
                f"{debug_dir.name}: {error}"
                for error in _validate_litellm_callbacks(
                    callback_path,
                    expected_model=spec.model,
                )
            )
        stdout_path = debug_dir / "stdout.log"
        stderr_path = debug_dir / "stderr.log"
        if stdout_path.is_file() and stderr_path.is_file():
            stdout_text = stdout_path.read_text(errors="replace")
            stderr_text = stderr_path.read_text(errors="replace")
            count_endpoint_succeeded = re.search(
                r'"POST /v1/messages/count_tokens(?:\?[^ ]*)? HTTP/[^"]+" 200 OK',
                stdout_text,
            )
            if count_endpoint_succeeded and "Falling back to local tokenizer." in stderr_text:
                recovered_token_count_statuses.update(
                    int(match)
                    for match in re.findall(
                        r"Provider token counting failed \((\d{3})\)",
                        stderr_text,
                    )
                )

    model_io = leaf / "model_io"
    request_dirs = sorted(path for path in model_io.iterdir() if path.is_dir()) if model_io.is_dir() else []
    if not request_dirs:
        errors.append("missing provider model_io requests")
    for request_dir in request_dirs:
        metadata_path = request_dir / "metadata.json"
        logical_path = request_dir / "logical_request.body"
        logical_json = request_dir / "logical_request.json"
        request_path = request_dir / "provider_request.body"
        request_json = request_dir / "provider_request.json"
        if not logical_path.is_file():
            errors.append(f"{request_dir.name}: missing logical_request.body")
        if not logical_json.is_file():
            errors.append(f"{request_dir.name}: missing logical_request.json")
        if not metadata_path.is_file():
            errors.append(f"{request_dir.name}: missing metadata.json")
            continue
        try:
            metadata = json.loads(metadata_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{request_dir.name}: invalid metadata: {exc}")
            continue
        if metadata.get("transport_complete") is not True:
            errors.append(f"{request_dir.name}: transport incomplete: {metadata.get('error')!r}")
        response = metadata.get("response")
        status = response.get("status") if isinstance(response, dict) else None
        request_metadata = metadata.get("request")
        request_url = request_metadata.get("url") if isinstance(request_metadata, dict) else None
        recovered_token_count = (
            isinstance(status, int)
            and status in recovered_token_count_statuses
            and isinstance(request_url, str)
            and request_url.split("?", 1)[0].rstrip("/").endswith("/responses/input_tokens")
        )
        if not isinstance(status, int) or (not 200 <= status < 300 and not recovered_token_count):
            errors.append(f"{request_dir.name}: provider HTTP status {status!r}")
        response_file = request_dir / str(response.get("file")) if isinstance(response, dict) and response.get("file") else None
        if response_file is None or not response_file.is_file():
            errors.append(f"{request_dir.name}: missing provider response bytes")
        else:
            try:
                captured_models = _captured_provider_models(response_file)
            except OSError as exc:
                errors.append(f"{request_dir.name}: unreadable provider response bytes: {exc}")
            else:
                expected_provider_model = spec.model.removeprefix("openrouter/")
                unexpected_models = sorted(captured_models - {expected_provider_model})
                if unexpected_models:
                    errors.append(
                        f"{request_dir.name}: provider response used unexpected model(s) "
                        f"{unexpected_models!r}; expected {expected_provider_model!r}"
                    )
        if not request_path.is_file():
            errors.append(f"{request_dir.name}: missing provider_request.body")
        if not request_json.is_file():
            errors.append(f"{request_dir.name}: missing provider_request.json")
        else:
            try:
                payload = json.loads(request_json.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{request_dir.name}: invalid provider_request.json: {exc}")
            else:
                reasoning = _reasoning_control_fields(payload)
                if reasoning:
                    errors.append(f"{request_dir.name}: default request contains reasoning controls: {reasoning}")
    return not errors, errors


def _run_arm(
    spec: ArmSpec,
    *,
    jobs_root: Path,
    timeout_sec: int,
) -> tuple[ArmSpec, int, list[str]]:
    env = dict(os.environ)
    env["SKILLSBENCH_JOBS_ROOT"] = str(jobs_root)
    with spec.log_path.open("wb") as log:
        process = subprocess.Popen(
            spec.command,
            cwd=SKILLSBENCH_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return_code = process.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            return_code = process.wait()
            log.write(f"\nlauncher timeout after {timeout_sec}s; process group killed\n".encode())
    ok, errors = validate_arm(spec, return_code)
    return spec, 0 if ok else return_code or 1, errors


def _run_cell(
    *,
    experiment_dir: Path,
    jobs_root: Path,
    model_name: str,
    model: str,
    harness: str,
    task_name: str,
    timeout_sec: int,
    arms: Sequence[str] = ARMS,
) -> dict:
    specs = [
        build_arm_spec(
            experiment_dir=experiment_dir,
            model_name=model_name,
            model=model,
            harness=harness,
            task_name=task_name,
            arm=arm,
        )
        for arm in arms
    ]
    results = []
    with ThreadPoolExecutor(max_workers=len(specs)) as pool:
        futures = {
            pool.submit(
                _run_arm,
                spec,
                jobs_root=jobs_root,
                timeout_sec=timeout_sec,
            ): spec
            for spec in specs
        }
        for future in as_completed(futures):
            spec = futures[future]
            try:
                spec, return_code, errors = future.result()
            except Exception as exc:
                return_code = 1
                errors = [f"launcher exception: {type(exc).__name__}: {exc}"]
            results.append(
                {
                    "arm": spec.arm,
                    "run_id": spec.run_id,
                    "run_leaf": str(spec.run_leaf),
                    "launcher_log": str(spec.log_path),
                    "return_code": return_code,
                    "errors": errors,
                }
            )
    results.sort(key=lambda item: item["arm"])
    return {
        "model_name": model_name,
        "model": model,
        "harness": harness,
        "task": task_name,
        "status": ("passed" if all(item["return_code"] == 0 for item in results) else "failed"),
        "arms": results,
    }


def _write_manifest(path: Path, payload: dict, lock: threading.Lock) -> None:
    with lock:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n")
        os.replace(temporary, path)


def _run_model_stage(
    *,
    experiment_dir: Path,
    jobs_root: Path,
    model_name: str,
    model: str,
    task_name: str,
    timeout_sec: int,
    harnesses: Sequence[str],
    arms: Sequence[str],
) -> list[dict]:
    results = []
    with ThreadPoolExecutor(max_workers=len(harnesses)) as pool:
        futures = [
            pool.submit(
                _run_cell,
                experiment_dir=experiment_dir,
                jobs_root=jobs_root,
                model_name=model_name,
                model=model,
                harness=harness,
                task_name=task_name,
                timeout_sec=timeout_sec,
                arms=arms,
            )
            for harness in harnesses
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item["harness"])


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.model_parallelism < 1:
        raise SystemExit("--model-parallelism must be at least 1")
    if args.timeout_sec < 1:
        raise SystemExit("--timeout-sec must be at least 1")
    try:
        selected = _selected_models(args.models)
        selected_harnesses = _selected_values(
            args.harnesses,
            allowed=HARNESSES,
            option="harnesses",
        )
        selected_arms = _selected_values(
            args.arms,
            allowed=ARMS,
            option="arms",
        )
        _validate_inputs()
        experiment_id = _safe_id(args.experiment_id or _default_experiment_id())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.stage2_only and any(name == "glm-5.2" for name, _ in selected):
        raise SystemExit("--stage2-only is unavailable for glm-5.2")

    experiment_dir = args.output_root.resolve() / experiment_id
    if experiment_dir.exists():
        raise SystemExit(f"refusing to reuse existing experiment: {experiment_dir}")

    if args.dry_run:
        print(f"DRY-RUN: experiment_dir={experiment_dir} model_parallelism={args.model_parallelism}")
        for model_name, model in selected:
            task_names = [] if args.stage2_only else ["rails-dev"]
            if args.stage2_only or (model_name != "glm-5.2" and not args.stage1_only):
                task_names.append("rust-router")
            for task_name in task_names:
                for harness in selected_harnesses:
                    for arm in selected_arms:
                        spec = build_arm_spec(
                            experiment_dir=experiment_dir,
                            model_name=model_name,
                            model=model,
                            harness=harness,
                            task_name=task_name,
                            arm=arm,
                        )
                        print(f"DRY-RUN {model_name}/{harness}/{task_name}/{arm}: {shlex.join(spec.command)}")
        return 0

    experiment_dir.mkdir(parents=True)
    (experiment_dir / "rollouts").mkdir()
    (experiment_dir / "launcher_logs").mkdir()
    jobs_root = experiment_dir / "raw_jobs"
    jobs_root.mkdir()
    manifest_path = experiment_dir / "manifest.json"
    manifest_lock = threading.Lock()
    manifest = {
        "experiment_id": experiment_id,
        "experiment_dir": str(experiment_dir),
        "jobs_root": str(jobs_root),
        "models": [model for _, model in selected],
        "harnesses": selected_harnesses,
        "arms": selected_arms,
        "reasoning": "default (omitted)",
        "model_parallelism": args.model_parallelism,
        "started_at": utcnow(),
        "status": "running",
        "stage1": [],
        "stage2": [],
    }
    _write_manifest(manifest_path, manifest, manifest_lock)
    print(f"experiment_dir: {experiment_dir}", flush=True)

    stage1_by_model: dict[str, list[dict]] = {}
    if not args.stage2_only:
        with ThreadPoolExecutor(max_workers=args.model_parallelism) as pool:
            futures = {
                pool.submit(
                    _run_model_stage,
                    experiment_dir=experiment_dir,
                    jobs_root=jobs_root,
                    model_name=model_name,
                    model=model,
                    task_name="rails-dev",
                    timeout_sec=args.timeout_sec,
                    harnesses=selected_harnesses,
                    arms=selected_arms,
                ): (model_name, model)
                for model_name, model in selected
            }
            for future in as_completed(futures):
                model_name, _model = futures[future]
                cells = future.result()
                stage1_by_model[model_name] = cells
                manifest["stage1"].extend(cells)
                _write_manifest(manifest_path, manifest, manifest_lock)
                status = "PASS" if all(cell["status"] == "passed" for cell in cells) else "FAIL"
                print(f"[stage1 {status}] {model_name}", flush=True)

    if not args.stage1_only:
        if args.stage2_only:
            eligible = selected
        else:
            eligible = [
                (model_name, model)
                for model_name, model in selected
                if model_name != "glm-5.2"
                and all(cell["status"] == "passed" for cell in stage1_by_model.get(model_name, []))
                and len(stage1_by_model.get(model_name, [])) == len(selected_harnesses)
            ]
        with ThreadPoolExecutor(max_workers=args.model_parallelism) as pool:
            futures = {
                pool.submit(
                    _run_model_stage,
                    experiment_dir=experiment_dir,
                    jobs_root=jobs_root,
                    model_name=model_name,
                    model=model,
                    task_name="rust-router",
                    timeout_sec=args.timeout_sec,
                    harnesses=selected_harnesses,
                    arms=selected_arms,
                ): model_name
                for model_name, model in eligible
            }
            for future in as_completed(futures):
                model_name = futures[future]
                cells = future.result()
                manifest["stage2"].extend(cells)
                _write_manifest(manifest_path, manifest, manifest_lock)
                status = "PASS" if all(cell["status"] == "passed" for cell in cells) else "FAIL"
                print(f"[stage2 {status}] {model_name}", flush=True)

    all_cells = [*manifest["stage1"], *manifest["stage2"]]
    failures = [cell for cell in all_cells if cell["status"] != "passed"]
    manifest["status"] = "passed" if not failures else "failed"
    manifest["ended_at"] = utcnow()
    manifest["failed_cells"] = len(failures)
    _write_manifest(manifest_path, manifest, manifest_lock)
    print(
        f"status: {manifest['status']}  cells={len(all_cells)} failed={len(failures)}",
        flush=True,
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
