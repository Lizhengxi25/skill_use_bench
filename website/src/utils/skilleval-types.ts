import type { TrajectorySummary } from "./trajectory-types";

// ── Grading results (LLM-as-judge) ────────────────────────────────────────
export interface CriterionResult {
  criterion_id: string;
  verdict: string; // "yes" | "no" | ...
  evidence_excerpt?: string;
  note?: string;
}

export interface PhaseGrade {
  phase: string;
  score: number;
  max_score: number;
  critical_passed?: boolean;
  critical_passed_per_step?: { module_id: number; critical_passed: boolean }[];
  criterion_results: CriterionResult[];
}

export interface TaskGrade {
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

// ── Static rubric (rubric.json) ───────────────────────────────────────────
export interface RubricCriterion {
  id: string;
  type?: string; // action | intent
  criticality?: string; // critical | supplementary
  criterion: string;
  evidence_target?: { artifact_type?: string; locator?: string; check?: string };
  pass_condition?: string;
}

export interface RubricModuleStep {
  module_id: number;
  module_name: string;
  module_description?: string;
  criteria: RubricCriterion[];
}

export interface RubricPhase {
  criteria?: RubricCriterion[];
  steps?: RubricModuleStep[];
  deferred_until_stage_2_1?: boolean;
}

export interface RubricDoc {
  skill?: string;
  category?: string;
  phases: Record<string, RubricPhase>;
  judge_protocol?: unknown;
}

// ── Per-trial trajectory payload (public/skilleval/<trialId>.json) ─────────
export interface JudgePhasePayload {
  phase: string;
  score: number;
  max_score: number;
  critical_passed?: boolean;
  log: string;
}

export interface TrialPayload {
  trajectory: TrajectorySummary;
  judge: JudgePhasePayload[];
}
