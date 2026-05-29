import path from "path";
import fs from "fs";
import { writeJsonOutput } from "./resolve-data-paths";
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

interface TaskResult {
  task: string; // taskKey
  model: string;
  modelShort: string;
  harness: string;
  family: string;
  reasoning: string;
  condition: "With Skills" | "No Skills";
  score: number; // micro-average % (mean across trials)
  trials: number;
  passCount: number;
  perfectCount: number;
}

function resolveRunDir(repoRoot: string, runDir: string): string {
  if (runDir) {
    const local = path.join(repoRoot, "runs", path.basename(runDir));
    if (fs.existsSync(local)) return local;
    if (fs.existsSync(runDir)) return runDir;
  }
  return runDir;
}

function generateResultsRegistry(): void {
  const repoRoot = resolveRepoRoot();
  const outputPath = path.join(__dirname, "..", "src", "data", "results-registry.json");

  // Group per (task, model, condition); each grade run contributes one "trial".
  const groups = new Map<
    string,
    { meta: TaskResult; pcts: number[] }
  >();

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
      const key = taskKey(run.dataset, id);
      const gk = `${key}|${ident.model}|${cond}`;
      if (!groups.has(gk)) {
        groups.set(gk, {
          meta: {
            task: key,
            model: ident.model,
            modelShort: ident.modelShort,
            harness: ident.harness,
            family: ident.family,
            reasoning: ident.reasoning,
            condition: cond,
            score: 0,
            trials: 0,
            passCount: 0,
            perfectCount: 0,
          },
          pcts: [],
        });
      }
      groups.get(gk)!.pcts.push(pct);
    }
  }

  const results: TaskResult[] = [];
  for (const { meta, pcts } of groups.values()) {
    const score = pcts.reduce((a, b) => a + b, 0) / pcts.length;
    results.push({
      ...meta,
      score: Math.round(score * 10) / 10,
      trials: pcts.length,
      passCount: pcts.filter((p) => p > 0).length,
      perfectCount: pcts.filter((p) => p >= 100).length,
    });
  }

  writeJsonOutput(outputPath, results);
  console.log(`[results] generated ${results.length} entries at ${outputPath}`);
}

generateResultsRegistry();
