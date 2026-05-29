import fs from "fs";
import path from "path";
import TOML from "@iarna/toml";
import { writeJsonOutput } from "./resolve-data-paths";
import {
  resolveRepoRoot,
  listGradeRuns,
  listTaskIds,
  taskKey,
} from "./skilleval-data";

interface Task {
  title: string; // stable join/route key, e.g. "group1-1"
  displayName: string; // skill name, shown as the page title
  skillName: string;
  taskId: string;
  dataset: string;
  category: string;
  difficulty: "easy" | "medium" | "hard";
  description: string;
  instruction: string;
  tags: string[];
  path: string;
  updatedAt: string;
  version: string;
  author_name: string;
  excluded?: boolean;
}

interface TaskToml {
  version?: string;
  metadata?: {
    task_id?: string;
    hidden_skill_name?: string;
    category?: string;
  };
}

function firstParagraph(instruction: string): string {
  const p = instruction.split("\n\n")[0].replace(/\n/g, " ").trim().slice(0, 300);
  return p.length >= 300 ? p + "..." : p;
}

function generateTasksRegistry(): void {
  const repoRoot = resolveRepoRoot();
  const outputPath = path.join(__dirname, "..", "src", "data", "tasks-registry.json");

  // Datasets in scope = those referenced by in-scope grade runs.
  const datasets = Array.from(new Set(listGradeRuns(repoRoot).map((r) => r.dataset))).filter(
    (d) => d && d !== "unknown",
  );

  const tasks: Task[] = [];
  for (const dataset of datasets) {
    const datasetDir = path.join(repoRoot, "tasks_runtime", dataset);
    if (!fs.existsSync(datasetDir)) {
      console.warn(`[tasks] dataset dir not found: ${datasetDir}`);
      continue;
    }
    for (const id of listTaskIds(datasetDir)) {
      const taskDir = path.join(datasetDir, id);
      const tomlPath = path.join(taskDir, "task.toml");
      const instructionPath = path.join(taskDir, "instruction.md");
      if (!fs.existsSync(tomlPath)) continue;

      let parsed: TaskToml = {};
      try {
        parsed = TOML.parse(fs.readFileSync(tomlPath, "utf-8")) as TaskToml;
      } catch (e) {
        console.error(`[tasks] bad task.toml for ${dataset}/${id}:`, e);
        continue;
      }

      const instruction = fs.existsSync(instructionPath)
        ? fs.readFileSync(instructionPath, "utf-8")
        : "";
      const skillName = parsed.metadata?.hidden_skill_name || `Task ${id}`;
      const stats = fs.statSync(tomlPath);

      tasks.push({
        title: taskKey(dataset, id),
        displayName: skillName,
        skillName,
        taskId: id,
        dataset,
        category: parsed.metadata?.category || "skill-eval",
        difficulty: "medium",
        description: firstParagraph(instruction),
        instruction,
        tags: skillName ? [skillName] : [],
        path: `tasks_runtime/${dataset}/${id}`,
        updatedAt: stats.mtime.toISOString(),
        version: parsed.version || "1.0",
        author_name: "",
      });
    }
  }

  // Stable order: by dataset then numeric id.
  tasks.sort(
    (a, b) => a.dataset.localeCompare(b.dataset) || Number(a.taskId) - Number(b.taskId),
  );

  writeJsonOutput(outputPath, tasks);
  console.log(
    `[tasks] generated ${tasks.length} tasks across ${datasets.length} dataset(s) at ${outputPath}`,
  );
}

generateTasksRegistry();
