# SkillsBench project extensions

`skillsbench_x` is the project-owned wrapper around SkillsBench 1.1 and
BenchFlow 0.6.5. It keeps the existing pipeline artifacts and candidate naming
stable while using the current BenchFlow evaluation CLI.

## Rollout contract

- The wrapper runs `bench eval run --tasks-dir ...`.
- `--with-skills` maps to `--skill-mode with-skill`; otherwise it uses
  `--skill-mode no-skill`.
- Registered candidate models run as `sandbox_user=agent`, matching SkillsBench
  1.1. The unregistered GPT-5.5 control path keeps its previous sandbox setting.
- OpenHands uses no ACP idle timeout and an LLM request timeout of 115200
  seconds.
- BenchFlow 0.6 always runs the verifier and captures its normalized LLM
  trajectory. Legacy `--skip-verify`, `--capture-workspace`, and
  `--capture-model-io` wrapper flags are accepted but are not forwarded because
  those CLI switches do not exist in BenchFlow 0.6.
- `default` is an experiment label meaning no reasoning effort is sent to the
  provider. It is not an alias for any named effort level.

The provider-boundary request filter removes harness-added reasoning fields for
`default`. For explicit efforts, it emits one OpenRouter
`reasoning: {"effort": ...}` control:

| Harness | Model selection | Explicit reasoning |
|---|---|---|
| Codex | process launch | process launch plus provider normalization |
| Claude Code | `ANTHROPIC_MODEL` | provider normalization |
| OpenHands | `LLM_MODEL` | `LLM_REASONING_EFFORT` plus provider normalization |

## OpenHands profiles

| OpenRouter model | Context | Max output | Allowed efforts |
|---|---:|---:|---|
| `openrouter/z-ai/glm-5.2` | 1,048,576 | 131,072 | `default`, `high`, `xhigh` |
| `openrouter/qwen/qwen3.5-397b-a17b` | 262,144 | 65,536 | `default` |
| `openrouter/openai/gpt-oss-120b` | 131,072 | 131,072 | `default`, `low`, `medium`, `high` |

All provider calls use `OPENROUTER_API_KEY`. The model profiles are the only
place that defines supported harnesses, effort ranges, context windows, output
limits, and Codex catalog metadata.
