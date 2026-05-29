import path from "path";
import fs from "fs";
import { writeJsonOutput } from "./resolve-data-paths";
import { reasoningRank } from "./experiments.config";
import {
  resolveRepoRoot,
  listGradeRuns,
  listTaskIds,
  taskKey,
  readMetadataTsv,
  modelIdentity,
  condition,
  readJudgePhase,
  microScore,
  type PhaseGrade,
} from "./skilleval-data";

const PHASE_ORDER = ["skill_identification", "module_sequence", "post_processing"];

interface LeaderboardEntry {
  harness: string;
  model: string;
  family: string;
  noSkills: number;
  noSkillsCi: number;
  withSkills: number;
  withSkillsCi: number;
  delta: number;
  normalizedGain: number;
  tasks: number;
  trialsPerTask: number;
  withTrials: number;
  noTrials: number;
  withCompletion: number;
  noCompletion: number;
  complete?: boolean;
}

interface Agg {
  harness: string;
  model: string;
  family: string;
  reasoning: string;
  with: Map<string, number>; // taskKey -> pct
  no: Map<string, number>;
}

function mean(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
}

function ci95(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  const variance = xs.reduce((a, b) => a + (b - m) ** 2, 0) / (xs.length - 1);
  return 1.96 * (Math.sqrt(variance) / Math.sqrt(xs.length));
}

function r1(x: number): number {
  return Math.round(x * 10) / 10;
}

function resolveRunDir(repoRoot: string, runDir: string): string {
  if (runDir) {
    const local = path.join(repoRoot, "runs", path.basename(runDir));
    if (fs.existsSync(local)) return local;
    if (fs.existsSync(runDir)) return runDir;
  }
  return runDir;
}

function generateLeaderboard(): void {
  const repoRoot = resolveRepoRoot();
  const outputPath = path.join(__dirname, "..", "src", "data", "leaderboard-data.json");

  const aggs = new Map<string, Agg>();

  for (const run of listGradeRuns(repoRoot)) {
    const runDir = resolveRunDir(repoRoot, run.runDir);
    for (const id of listTaskIds(run.gradeDir)) {
      const meta = readMetadataTsv(runDir, id);
      if (!meta) continue;
      const ident = modelIdentity(meta);
      const cond = condition(meta);
      const phases: PhaseGrade[] = [];
      for (const phase of PHASE_ORDER) {
        const g = readJudgePhase(run.gradeDir, id, phase);
        if (g) phases.push(g);
      }
      if (phases.length === 0) continue;
      const { pct } = microScore(phases);
      const key = `${ident.harness}::${ident.model}`;
      if (!aggs.has(key)) {
        aggs.set(key, {
          harness: ident.harness,
          model: ident.model,
          family: ident.family,
          reasoning: ident.reasoning,
          with: new Map(),
          no: new Map(),
        });
      }
      const a = aggs.get(key)!;
      (cond === "With Skills" ? a.with : a.no).set(taskKey(run.dataset, id), pct);
    }
  }

  const rows: LeaderboardEntry[] = [];
  for (const a of aggs.values()) {
    const withPcts = [...a.with.values()];
    const noPcts = [...a.no.values()];
    const taskUnion = new Set([...a.with.keys(), ...a.no.keys()]);
    const tasks = taskUnion.size;
    const withSkills = mean(withPcts);
    const noSkills = mean(noPcts);
    const normalizedGain =
      noPcts.length && noSkills < 100 ? ((withSkills - noSkills) / (100 - noSkills)) * 100 : 0;
    rows.push({
      harness: a.harness,
      model: a.model,
      family: a.family,
      noSkills: r1(noSkills),
      noSkillsCi: r1(ci95(noPcts)),
      withSkills: r1(withSkills),
      withSkillsCi: r1(ci95(withPcts)),
      delta: r1(withSkills - noSkills),
      normalizedGain: r1(normalizedGain),
      tasks,
      trialsPerTask: 1,
      withTrials: withPcts.length,
      noTrials: noPcts.length,
      withCompletion: tasks ? r1((withPcts.length / tasks) * 100) : 0,
      noCompletion: tasks ? r1((noPcts.length / tasks) * 100) : 0,
      complete: withPcts.length > 0 && noPcts.length > 0,
    });
  }

  // Stable order: by reasoning effort then model name. UI re-sorts on demand.
  const order = new Map([...aggs.entries()].map(([, a]) => [a.model, reasoningRank(a.reasoning)]));
  rows.sort(
    (x, y) => (order.get(x.model)! - order.get(y.model)!) || x.model.localeCompare(y.model),
  );

  writeJsonOutput(outputPath, rows);
  console.log(`[leaderboard] generated ${rows.length} rows at ${outputPath}`);
}

generateLeaderboard();
