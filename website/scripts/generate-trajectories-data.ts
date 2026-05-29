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
  readJudgeLog,
  microScore,
  readTrajectoryJsonl,
  readResultTimingSec,
  type PhaseGrade,
} from "./skilleval-data";
import { parseSkillEvalFlat } from "../src/utils/trajectory-parser";

const PHASE_ORDER = ["skill_identification", "module_sequence", "post_processing"];
const JUDGE_LOG_CAP = 40000;

interface IndexEntry {
  task: string;
  trialId: string;
  model: string;
  modelShort: string;
  harness: string;
  family: string;
  reasoning: string;
  condition: "With Skills" | "No Skills";
  reward: number; // micro-avg fraction 0..1
  execTimeSec: number;
  agentName: string;
  /** Static, pre-parsed payload (steps + judge logs). */
  payloadUrl: string;
}

interface JudgePhasePayload {
  phase: string;
  score: number;
  max_score: number;
  critical_passed?: boolean;
  log: string;
}

function resolveRunDir(repoRoot: string, runDir: string): string {
  if (runDir) {
    const local = path.join(repoRoot, "runs", path.basename(runDir));
    if (fs.existsSync(local)) return local;
    if (fs.existsSync(runDir)) return runDir;
  }
  return runDir;
}

function makeTrialKey(tk: string, modelKey: string, cond: string): string {
  const m = modelKey
    .replace(/[^a-zA-Z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "")
    .toLowerCase();
  const c = cond === "With Skills" ? "with" : "without";
  return `${tk}__${m}__${c}`;
}

function generateTrajectoriesData(): void {
  const repoRoot = resolveRepoRoot();
  const indexOutputPath = path.join(__dirname, "..", "src", "data", "trajectories-index.json");
  const payloadDir = path.join(__dirname, "..", "public", "skilleval");

  // Clear stale payloads, then recreate.
  if (fs.existsSync(payloadDir)) fs.rmSync(payloadDir, { recursive: true, force: true });
  fs.mkdirSync(payloadDir, { recursive: true });

  const index: IndexEntry[] = [];
  let payloadCount = 0;

  for (const run of listGradeRuns(repoRoot)) {
    const runDir = resolveRunDir(repoRoot, run.runDir);

    for (const id of listTaskIds(run.gradeDir)) {
      const meta = readMetadataTsv(runDir, id);
      if (!meta) continue;
      const ident = modelIdentity(meta);
      const cond = condition(meta);
      const tk = taskKey(run.dataset, id);
      const trialId = makeTrialKey(tk, ident.modelKey, cond);

      // Scores (reward = micro-avg fraction).
      const phases: PhaseGrade[] = [];
      for (const phase of PHASE_ORDER) {
        const g = readJudgePhase(run.gradeDir, id, phase);
        if (g) phases.push(g);
      }
      const micro = microScore(phases);
      const reward = micro.max > 0 ? micro.score / micro.max : 0;
      const execTimeSec = readResultTimingSec(runDir, id);

      // Parse agent trajectory.
      const raw = readTrajectoryJsonl(runDir, id);
      const steps = raw ? parseSkillEvalFlat(raw) : [];

      // Judge logs (raw .agent.log, capped).
      const judge: JudgePhasePayload[] = [];
      for (const phase of PHASE_ORDER) {
        const g = readJudgePhase(run.gradeDir, id, phase);
        if (!g) continue;
        const log = (readJudgeLog(run.gradeDir, id, phase) || "").slice(0, JUDGE_LOG_CAP);
        judge.push({
          phase,
          score: g.score,
          max_score: g.max_score,
          critical_passed: g.critical_passed,
          log,
        });
      }

      const payload = {
        trajectory: {
          task: tk,
          trialId,
          model: ident.model,
          modelShort: ident.modelShort,
          harness: ident.harness,
          family: ident.family,
          condition: cond,
          reward,
          execTimeSec,
          totalSteps: steps.length,
          steps,
        },
        judge,
      };
      fs.writeFileSync(path.join(payloadDir, `${trialId}.json`), JSON.stringify(payload));
      payloadCount++;

      index.push({
        task: tk,
        trialId,
        model: ident.model,
        modelShort: ident.modelShort,
        harness: ident.harness,
        family: ident.family,
        reasoning: ident.reasoning,
        condition: cond,
        reward,
        execTimeSec,
        agentName: "skilleval",
        payloadUrl: `/skilleval/${trialId}.json`,
      });
    }
  }

  writeJsonOutput(indexOutputPath, index);
  console.log(
    `[trajectories] generated ${index.length} index entries + ${payloadCount} payloads at ${payloadDir}`,
  );
}

generateTrajectoriesData();
