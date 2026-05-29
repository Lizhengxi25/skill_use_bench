import path from "path";
import fs from "fs";
import { writeJsonOutput } from "./resolve-data-paths";
import {
  resolveRepoRoot,
  listGradeRuns,
  listTaskIds,
  taskKey,
  readRubricJson,
} from "./skilleval-data";

/**
 * Emit src/data/rubrics-registry.json: { taskKey -> rubric.json } (verbatim
 * structure: phases.skill_identification.criteria[], phases.module_sequence.steps[],
 * phases.post_processing.criteria[], judge_protocol). The Rubric & Grading tab
 * pairs this static rubric with per-model grading verdicts from grades-registry.json.
 */
function generateRubricsRegistry(): void {
  const repoRoot = resolveRepoRoot();
  const outputPath = path.join(__dirname, "..", "src", "data", "rubrics-registry.json");

  const datasets = Array.from(new Set(listGradeRuns(repoRoot).map((r) => r.dataset))).filter(
    (d) => d && d !== "unknown",
  );

  const rubrics: Record<string, unknown> = {};
  for (const dataset of datasets) {
    const datasetDir = path.join(repoRoot, "tasks_runtime", dataset);
    if (!fs.existsSync(datasetDir)) continue;
    for (const id of listTaskIds(datasetDir)) {
      const rubric = readRubricJson(datasetDir, id);
      if (rubric) rubrics[taskKey(dataset, id)] = rubric;
    }
  }

  writeJsonOutput(outputPath, rubrics);
  console.log(`[rubrics] generated ${Object.keys(rubrics).length} rubrics at ${outputPath}`);
}

generateRubricsRegistry();
