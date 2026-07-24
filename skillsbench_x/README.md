# SkillsBench project extensions

`skillsbench_x` is the project-owned wrapper around SkillsBench 1.1 and
BenchFlow 0.6.5. It keeps the existing pipeline artifacts and candidate naming
stable while using the current BenchFlow evaluation CLI.

## SkillsBench 1.1 alignment

- The wrapper runs `bench eval run --tasks-dir ...`.
- `--with-skills` maps to `--skill-mode with-skill`; otherwise it uses
  `--skill-mode no-skill`.
- Both SkillsBench 1.1 `task.md` tasks and legacy `task.toml` plus
  `instruction.md` tasks are discovered.
- The legacy prompt prefix is composed with the task instruction and passed
  through `--prompt`, because the old `bench run --prompt-prefix` option is no
  longer part of the 0.6 evaluation CLI.
- Registered candidate models run as `sandbox_user=agent`, matching SkillsBench
  1.1. The unregistered GPT-5.5 control path keeps its previous sandbox setting.
- OpenHands uses no ACP idle timeout and an LLM request timeout of 115200
  seconds.
- With-skill prompts use each harness's native discovery path: Codex reads
  `$HOME/.agents/skills/`, Claude Code reads `$HOME/.claude/skills/`, and
  OpenHands reads `/app/.agents/skills/`.
- BenchFlow 0.6 always runs the verifier and captures its normalized LLM
  trajectory. The wrapper translates `--capture-workspace` into
  `BENCHFLOW_CAPTURE_WORKSPACE=1`, and forwards `--capture-model-io` to
  BenchFlow's explicit opt-in final-provider capture. Legacy `--skip-verify`
  remains an accepted no-op.
- `default` is an experiment label meaning no reasoning effort is sent to the
  provider. It is not an alias for any named effort level.

The persistent judge transcript prefers, in order:

1. a legacy harvested Codex native session, when an older BenchFlow produced it;
2. BenchFlow 0.6 `trajectory/llm_trajectory.jsonl`, normalized and redacted by
   BenchFlow's structured trajectory parser;
3. the ACP trajectory fallback.

The structured provider trajectory retains model-visible tool calls and tool
results without rendering raw chain of thought. The persistent run leaf keeps
BenchFlow's `config.json`, `timing.json`, and `prompts.json`, plus the complete
`agent/`, `trajectory/`, `verifier/`, `artifacts/`, and optional `workspace/`
debug trees. Existing top-level compatibility files such as `result.json`,
`trajectory.jsonl`, `trajectory.log`, `llm_trajectory.jsonl`, `reward.txt`,
`workspace.tgz`, and agent stdout/stderr remain available.

The GPT-5.5 control remains Codex-only. Its model and explicit reasoning effort
are now selected when `codex-acp` starts, its existing `workspace-write` Codex
sandbox setting is retained, and the invalid `tools.web_search=false` override
is absent. No OpenRouter model profile is applied to that control.

## Provider controls

The provider-boundary request filter removes harness-added reasoning fields for
`default`. For explicit efforts, it emits one OpenRouter
`reasoning: {"effort": ...}` control:

| Harness | Model selection | Explicit reasoning |
|---|---|---|
| Codex | process launch | process launch plus provider normalization |
| Claude Code | `ANTHROPIC_MODEL` | provider normalization |
| OpenHands | `LLM_MODEL` | `LLM_REASONING_EFFORT` plus provider normalization |

All OpenRouter traffic is routed through BenchFlow's metered LiteLLM gateway.
The upstream endpoint is `https://openrouter.ai/api/v1`, and the only upstream
credential is `OPENROUTER_API_KEY`.

## Registered profiles

| OpenRouter model | Context | Max output | Allowed efforts | Harnesses |
|---|---:|---:|---|---|
| `openrouter/tencent/hy3` | 262,144 | 65,536 | `default`, `none`, `low`, `high` | Codex, Claude Code, OpenHands |
| `openrouter/deepseek/deepseek-v4-flash` | 1,048,576 | 131,072 | `default`, `high`, `xhigh` | Codex, Claude Code, OpenHands |
| `openrouter/z-ai/glm-5.2` | 1,048,576 | 131,072 | `default`, `high`, `xhigh` | Codex, Claude Code, OpenHands |
| `openrouter/qwen/qwen3.5-397b-a17b` | 262,144 | 65,536 | `default` | Codex, Claude Code, OpenHands |
| `openrouter/openai/gpt-oss-120b` | 131,072 | 65,536 | `default`, `low`, `medium`, `high` | Codex, Claude Code, OpenHands |

The existing DeepSeek V4 Pro, Kimi K2.6, and Qwen3-Coder-Next profiles remain
available on their previously registered Codex and Claude Code harnesses. Kimi
K3 and Kimi K2.7 Code remain unregistered.

The model profile registry is the only project-owned place that defines
supported harnesses, effort ranges, context windows, output limits, and Codex
catalog metadata. The integration does not globally disable public skills,
OpenHands subagents, or Codex multi-agent features.

## OpenHands pin

OpenHands is reproducibly installed from:

- OpenHands CLI commit
  `2df8a2835d3f1bd2f2eadf5a7a2e1ad0dfb0d271` (CLI version 1.16.0);
- `openhands-sdk==1.28.1`;
- `openhands-tools==1.28.1`.

That CLI commit itself declares the same SDK/tools versions. It is not a
floating branch or tag. BenchFlow writes `base_url`, model, timeout,
`max_input_tokens`, `max_output_tokens`, and optional reasoning controls into
OpenHands settings before launching `openhands acp --always-approve
--override-with-envs`.
