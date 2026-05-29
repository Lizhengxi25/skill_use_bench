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

/** Display order for phases regardless of manifest ordering. */
const PHASE_ORDER = ["skill_identification", "module_sequence", "post_processing"];

interface TaskGrade {
  taskKey: string;
  dataset: string;
  taskId: string;
  modelKey: string;
  model: string;
  modelShort: string;
  harness: string;
  family: string;
  reasoning: string;
  condition: "With Skills" | "No Skills";
  gradeId: string;
  phases: PhaseGrade[];
  total: { score: number; max: number; pct: number };
}

/** Prefer runs/<basename> under the repo root; fall back to the manifest's abs path. */
function resolveRunDir(repoRoot: string, runDir: string): string {
  if (runDir) {
    const local = path.join(repoRoot, "runs", path.basename(runDir));
    if (fs.existsSync(local)) return local;
    if (fs.existsSync(runDir)) return runDir;
  }
  return runDir;
}

function orderedPhases(manifestPhases: string[]): string[] {
  const known = PHASE_ORDER.filter((p) => manifestPhases.length === 0 || manifestPhases.includes(p));
  const extra = manifestPhases.filter((p) => !PHASE_ORDER.includes(p));
  return [...known, ...extra];
}

function generateGradesRegistry(): void {
  const repoRoot = resolveRepoRoot();
  const outputPath = path.join(__dirname, "..", "src", "data", "grades-registry.json");

  const out: TaskGrade[] = [];
  let skippedNoMeta = 0;

  for (const run of listGradeRuns(repoRoot)) {
    const runDir = resolveRunDir(repoRoot, run.runDir);
    const phaseNames = orderedPhases(run.phases);

    for (const id of listTaskIds(run.gradeDir)) {
      const meta = readMetadataTsv(runDir, id);
      if (!meta) {
        skippedNoMeta++;
        continue;
      }
      const ident = modelIdentity(meta);
      const phases: PhaseGrade[] = [];
      for (const phase of phaseNames) {
        const g = readJudgePhase(run.gradeDir, id, phase);
        if (g) phases.push(g);
      }
      if (phases.length === 0) continue;

      out.push({
        taskKey: taskKey(run.dataset, id),
        dataset: run.dataset,
        taskId: id,
        modelKey: ident.modelKey,
        model: ident.model,
        modelShort: ident.modelShort,
        harness: ident.harness,
        family: ident.family,
        reasoning: ident.reasoning,
        condition: condition(meta),
        gradeId: run.gradeId,
        phases,
        total: microScore(phases),
      });
    }
  }

  writeJsonOutput(outputPath, out);
  console.log(
    `[grades] generated ${out.length} task-grade entries at ${outputPath}` +
      (skippedNoMeta ? ` (skipped ${skippedNoMeta} tasks with no metadata.tsv)` : ""),
  );
}

generateGradesRegistry();
