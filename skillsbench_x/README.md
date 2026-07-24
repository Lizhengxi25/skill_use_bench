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
  `$HOME/.agents/skills/`; Claude Code is linked at both
  `$HOME/.claude/skills/` and `/app/.claude/skills/`, with the Docker smoke
  prompt using the latter so a non-Claude model does not have to guess the
  sandbox user's home; OpenHands reads `/app/.agents/skills/`.
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
| Claude Code | ACP `session/set_model` | provider normalization |
| OpenHands | `LLM_MODEL` | `LLM_REASONING_EFFORT` plus provider normalization |

All OpenRouter traffic is routed through BenchFlow's metered LiteLLM gateway.
The upstream endpoint is `https://openrouter.ai/api/v1`, and the only upstream
credential is `OPENROUTER_API_KEY`.

## Registered profiles

| OpenRouter model | Context | Max output | Allowed efforts | Harnesses |
|---|---:|---:|---|---|
| `openrouter/tencent/hy3` | 262,144 | 65,536 | `default`, `none`, `low`, `high` | Codex, Claude Code, OpenHands |
| `openrouter/z-ai/glm-5.1` | 200,000 | 128,000 | `default` | Codex, Claude Code, OpenHands |
| `openrouter/moonshotai/kimi-k2.6` | 262,144 | 65,536 | `default` | Codex, Claude Code, OpenHands |
| `openrouter/minimax/minimax-m3` | 524,288 | 131,072 | `default`, `none`, `low`, `medium`, `high` | Codex, Claude Code, OpenHands |
| `openrouter/minimax/minimax-m2.7` | 196,608 | 131,072 | `default`, `none`, `low`, `medium`, `high` | Codex, Claude Code, OpenHands |
| `openrouter/deepseek/deepseek-v4-pro` | 1,048,576 | 131,072 | `default`, `high`, `xhigh` | Codex, Claude Code, OpenHands |
| `openrouter/deepseek/deepseek-v4-flash` | 1,048,576 | 131,072 | `default`, `high`, `xhigh` | Codex, Claude Code, OpenHands |
| `openrouter/z-ai/glm-5.2` | 1,048,576 | 131,072 | `default`, `high`, `xhigh` | Codex, Claude Code, OpenHands |
| `openrouter/qwen/qwen3.5-397b-a17b` | 262,144 | 65,536 | `default` | Codex, Claude Code, OpenHands |
| `openrouter/openai/gpt-oss-120b` | 131,072 | 65,536 | `default`, `low`, `medium`, `high` | Codex, Claude Code, OpenHands |

Qwen3-Coder-Next remains available on Codex and Claude Code. Kimi K3 and Kimi
K2.7 Code remain unregistered.

The model profile registry is the only project-owned place that defines
supported harnesses, effort ranges, context windows, output limits, and Codex
catalog metadata. The integration does not globally disable public skills,
OpenHands subagents, or Codex multi-agent features.

## Harness pins

The Claude Code harness is fixed to:

- Zed `claude-agent-acp` source commit
  `670fb18728514c367cf600925c475bb2bd123914` (adapter version `0.13.1`);
- `@anthropic-ai/claude-agent-sdk==0.2.19`;
- the SDK-bundled Claude Code `2.1.19`.

The SkillsBench v1.1-tagged experiment configs record direct Claude Code
`2.1.19`; that direct-CLI pin is the harness-version evidence. No published npm
adapter contains the exact matching SDK, so BenchFlow builds the immutable Zed
source commit above and verifies all three versions at install time. Mixing a
newer adapter/SDK with the old CLI can deliver final model text without closing
the ACP turn. The historical adapter accepts OpenRouter aliases through
`session/set_model`.

BenchFlow also applies one fail-closed patch to the pinned Claude Code
`2.1.19` file: it disables the optional post-Bash path-extraction prefetch.
That background feature otherwise sends extra requests to Claude Code's
hard-coded auxiliary model and can keep streaming after the ACP turn has
already completed. Installation requires exactly one matching source
signature, so an upstream byte change cannot silently receive the patch.

The first-party OpenHands components are fixed to:

- OpenHands CLI commit
  `3ca17446c5d9c1e35e054803478a3501ec251ecf` (CLI version 1.16.0);
- `openhands-sdk==1.22.1`;
- `openhands-tools==1.22.1`;
- `openhands-workspace==1.11.1`;
- `openhands-agent-server==1.9.1`.

The CLI source declares SDK/tools 1.21.0; the SkillsBench v1.1 BenchFlow
runtime deliberately overrides both to 1.22.1. Its own lock fixes agent-server
at 1.9.1, while workspace otherwise leaves that dependency unbounded, so
BenchFlow pins the complete first-party closure explicitly.
Third-party Python dependencies are still resolver-managed rather than a
byte-for-byte runtime lock.

This is a historical-fidelity pin, not the newest known stability line.
BenchFlow PR #921 later moved SDK/tools to 1.28.1 to address long-running ACP,
terminal, tool-executor, and event-log issues. The readiness smokes cover
installation and short tasks; large fleets should treat long-run behavior as a
separate validation gate.

BenchFlow writes `base_url`, model, timeout,
`max_input_tokens`, `max_output_tokens`, and optional reasoning controls into
OpenHands settings before launching `openhands acp --always-approve
--override-with-envs`.
