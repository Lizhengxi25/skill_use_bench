/**
 * Disk-discovery helpers shared by every skill-eval generator script.
 *
 * Data model (all derived from disk, no hand-maintained mapping):
 *   grades/<grade_id>/manifest.json      -> run_dir, rubric_root, phases
 *   grades/<grade_id>/<task>/judge_phase_<phase>.json   -> verdicts + score/max_score
 *   grades/<grade_id>/<task>/judge_phase_<phase>.agent.log  -> judge reasoning log
 *   runs/<run_id>/<task>/metadata.tsv    -> model, reasoning_effort, with_skills, agent
 *   runs/<run_id>/<task>/result.json     -> timing
 *   runs/<run_id>/<task>/trajectory.jsonl -> ACP flat-JSONL agent trajectory
 *   tasks_runtime/<dataset>/<task>/{task.toml,instruction.md,rubric/rubric.json}
 */
import fs from "fs";
import path from "path";
import {
  GRADE_INCLUDE_PREFIXES,
  GRADE_EXCLUDE_PREFIXES,
  modelInfo,
  harnessLabel,
} from "./experiments.config";

/** Locate the skillsbench repo root (parent of website/). */
export function resolveRepoRoot(): string {
  const candidates = [
    path.join(__dirname, "..", ".."), // website/scripts -> repo root
    path.join(__dirname, ".."), // website -> (rare layout)
  ];
  for (const c of candidates) {
    if (fs.existsSync(path.join(c, "grades")) && fs.existsSync(path.join(c, "tasks_runtime"))) {
      return path.resolve(c);
    }
  }
  // Best-effort fallback; downstream existsSync checks will warn.
  return path.resolve(path.join(__dirname, "..", ".."));
}

function included(name: string): boolean {
  if (GRADE_EXCLUDE_PREFIXES.some((p) => name.startsWith(p))) return false;
  if (GRADE_INCLUDE_PREFIXES.length === 0) return true;
  return GRADE_INCLUDE_PREFIXES.some((p) => name.startsWith(p));
}

export interface GradeRun {
  gradeId: string;
  gradeDir: string; // abs path to grades/<grade_id>
  runDir: string; // abs path to runs/<run_id> (from manifest)
  rubricRoot: string; // abs path to tasks_runtime/<dataset> (from manifest)
  dataset: string; // basename of rubricRoot, e.g. "20260601-group1"
  phases: string[];
}

interface Manifest {
  grade_id?: string;
  run_dir?: string;
  rubric_root?: string;
  phases?: string[];
}

/** All in-scope grade runs under grades/. */
export function listGradeRuns(repoRoot: string): GradeRun[] {
  const gradesRoot = path.join(repoRoot, "grades");
  if (!fs.existsSync(gradesRoot)) {
    console.warn(`[skilleval] grades/ not found at ${gradesRoot}`);
    return [];
  }
  const runs: GradeRun[] = [];
  for (const entry of fs.readdirSync(gradesRoot, { withFileTypes: true })) {
    if (!entry.isDirectory() || !included(entry.name)) continue;
    const gradeDir = path.join(gradesRoot, entry.name);
    const manifestPath = path.join(gradeDir, "manifest.json");
    if (!fs.existsSync(manifestPath)) continue;
    let m: Manifest;
    try {
      m = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
    } catch {
      console.warn(`[skilleval] bad manifest: ${manifestPath}`);
      continue;
    }
    const rubricRoot = m.rubric_root || "";
    runs.push({
      gradeId: m.grade_id || entry.name,
      gradeDir,
      runDir: m.run_dir || "",
      rubricRoot,
      dataset: rubricRoot ? path.basename(rubricRoot) : "unknown",
      phases: m.phases || [],
    });
  }
  return runs;
}

/** Numeric task ids (as strings) present in a grade dir, sorted numerically. */
export function listTaskIds(gradeDir: string): string[] {
  if (!fs.existsSync(gradeDir)) return [];
  return fs
    .readdirSync(gradeDir, { withFileTypes: true })
    .filter((e) => e.isDirectory() && /^\d+$/.test(e.name))
    .map((e) => e.name)
    .sort((a, b) => Number(a) - Number(b));
}

/** Strip a leading YYYYMMDD- date from a dataset dir name: "20260601-group1" -> "group1". */
export function datasetSlug(dataset: string): string {
  return dataset.replace(/^\d{8}-/, "");
}

/** Stable, URL-safe join/route key for a task. */
export function taskKey(dataset: string, taskId: string): string {
  return `${datasetSlug(dataset)}-${taskId}`;
}

export type MetadataTsv = Record<string, string>;

export function readMetadataTsv(runDir: string, taskId: string): MetadataTsv | null {
  const p = path.join(runDir, taskId, "metadata.tsv");
  if (!fs.existsSync(p)) return null;
  const out: MetadataTsv = {};
  for (const line of fs.readFileSync(p, "utf-8").split("\n")) {
    if (!line.trim()) continue;
    const tab = line.indexOf("\t");
    if (tab === -1) continue;
    out[line.slice(0, tab).trim()] = line.slice(tab + 1).trim();
  }
  return out;
}

export interface ModelIdentity {
  /** Join key for grouping with/without across a model+effort. */
  modelKey: string;
  /** Leaderboard / picker label, e.g. "GPT-5.5 (high)". */
  model: string;
  /** Short label shown next to the harness, e.g. "GPT-5.5 · high". */
  modelShort: string;
  harness: string;
  family: string;
  reasoning: string;
}

export function modelIdentity(meta: MetadataTsv): ModelIdentity {
  const rawModel = meta.model || "unknown";
  const reasoning = meta.reasoning_effort || "default";
  const info = modelInfo(rawModel);
  return {
    modelKey: `${rawModel}::${reasoning}`,
    model: `${info.displayBase} (${reasoning})`,
    modelShort: `${info.displayBase} · ${reasoning}`,
    harness: harnessLabel(meta.agent || ""),
    family: info.family,
    reasoning,
  };
}

export type Condition = "With Skills" | "No Skills";

export function condition(meta: MetadataTsv): Condition {
  const v = (meta.with_skills || "").toLowerCase();
  return v === "true" || v === "1" || v === "yes" ? "With Skills" : "No Skills";
}

export interface PhaseGrade {
  phase: string;
  score: number;
  max_score: number;
  critical_passed?: boolean;
  critical_passed_per_step?: { module_id: number; critical_passed: boolean }[];
  criterion_results: {
    criterion_id: string;
    verdict: string;
    evidence_excerpt?: string;
    note?: string;
  }[];
}

/** Read one phase's judge output. Returns null when missing/unparseable. */
export function readJudgePhase(gradeDir: string, taskId: string, phase: string): PhaseGrade | null {
  const p = path.join(gradeDir, taskId, `judge_phase_${phase}.json`);
  if (!fs.existsSync(p)) return null;
  try {
    const j = JSON.parse(fs.readFileSync(p, "utf-8"));
    return {
      phase: j.phase || phase,
      score: typeof j.score === "number" ? j.score : 0,
      max_score: typeof j.max_score === "number" ? j.max_score : 0,
      critical_passed: j.critical_passed,
      critical_passed_per_step: j.critical_passed_per_step,
      criterion_results: Array.isArray(j.criterion_results) ? j.criterion_results : [],
    };
  } catch {
    return null;
  }
}

/** Read the raw judge reasoning log for a phase (the .agent.log). */
export function readJudgeLog(gradeDir: string, taskId: string, phase: string): string | null {
  const p = path.join(gradeDir, taskId, `judge_phase_${phase}.agent.log`);
  if (!fs.existsSync(p)) return null;
  try {
    return fs.readFileSync(p, "utf-8");
  } catch {
    return null;
  }
}

/** Micro-average across phases: Σscore / Σmax → normalized %. */
export function microScore(phases: PhaseGrade[]): { score: number; max: number; pct: number } {
  let score = 0;
  let max = 0;
  for (const ph of phases) {
    score += ph.score;
    max += ph.max_score;
  }
  const pct = max > 0 ? (score / max) * 100 : 0;
  return { score, max, pct: Math.round(pct * 10) / 10 };
}

export function readTaskToml(rubricRoot: string, taskId: string): string | null {
  const p = path.join(rubricRoot, taskId, "task.toml");
  return fs.existsSync(p) ? fs.readFileSync(p, "utf-8") : null;
}

export function readInstruction(rubricRoot: string, taskId: string): string {
  const p = path.join(rubricRoot, taskId, "instruction.md");
  return fs.existsSync(p) ? fs.readFileSync(p, "utf-8") : "";
}

export function readRubricJson(rubricRoot: string, taskId: string): unknown | null {
  const p = path.join(rubricRoot, taskId, "rubric", "rubric.json");
  if (!fs.existsSync(p)) return null;
  try {
    return JSON.parse(fs.readFileSync(p, "utf-8"));
  } catch {
    return null;
  }
}

export function readTrajectoryJsonl(runDir: string, taskId: string): string | null {
  const p = path.join(runDir, taskId, "trajectory.jsonl");
  return fs.existsSync(p) ? fs.readFileSync(p, "utf-8") : null;
}

export function readResultTimingSec(runDir: string, taskId: string): number {
  const p = path.join(runDir, taskId, "result.json");
  if (!fs.existsSync(p)) return 0;
  try {
    const r = JSON.parse(fs.readFileSync(p, "utf-8"));
    const t = r?.timing?.total ?? r?.timing?.agent_execution;
    return typeof t === "number" ? Math.round(t) : 0;
  } catch {
    return 0;
  }
}
