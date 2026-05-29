/**
 * Single hand-edited extension surface for the skill-eval website.
 *
 * Everything else is discovered from disk (grades/<id>/manifest.json + the run's
 * metadata.tsv). To add a new experiment you normally only drop files on disk and
 * re-run `bun run generate-all`. The only edits you ever make here are:
 *   - a new grade-dir prefix (GRADE_INCLUDE_PREFIXES), if you name runs differently
 *   - a brand-new model id (MODEL_MAP) or harness (HARNESS_MAP)
 */

/** Grade directories under grades/ are surfaced only when their name starts with
 *  one of these prefixes. Leave empty to include everything not excluded. */
export const GRADE_INCLUDE_PREFIXES: string[] = ["group1-"];

/** ...and never when the name starts with one of these (smoke tests, sweeps, scratch). */
export const GRADE_EXCLUDE_PREFIXES: string[] = ["_", "smoke", "ab-effort"];

export interface ModelInfo {
  /** Brand family — must have a colour in src/data/leaderboard-data.ts BRAND_COLORS. */
  family: string;
  /** Human label without the reasoning effort, e.g. "GPT-5.5". */
  displayBase: string;
}

/** Keyed by the raw `model` value in runs/<run>/<task>/metadata.tsv. */
export const MODEL_MAP: Record<string, ModelInfo> = {
  "gpt-5.5": { family: "openai", displayBase: "GPT-5.5" },
  "gpt-5.4-mini": { family: "openai", displayBase: "GPT-5.4 Mini" },
};

export function modelInfo(rawModel: string): ModelInfo {
  return MODEL_MAP[rawModel] ?? { family: "openai", displayBase: rawModel };
}

/** Display order for reasoning efforts (low → high). Unknown efforts sort last. */
export const REASONING_ORDER: string[] = ["minimal", "low", "medium", "high", "xhigh"];

export function reasoningRank(reasoning: string): number {
  const i = REASONING_ORDER.indexOf(reasoning);
  return i === -1 ? REASONING_ORDER.length : i;
}

/** Keyed by the raw `agent` value in metadata.tsv. */
export const HARNESS_MAP: Record<string, string> = {
  "codex-acp": "Codex",
  codex: "Codex",
  "claude-agent-acp": "Claude Code",
  "claude-code": "Claude Code",
  "gemini-cli": "Gemini CLI",
};

export function harnessLabel(agent: string): string {
  return HARNESS_MAP[agent] ?? agent;
}

/** GitHub repo used for "View on GitHub" links on task pages. */
export const GITHUB_REPO_BASE =
  "https://github.com/benchflow-ai/skillsbench/tree/main";
