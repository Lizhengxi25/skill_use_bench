"use client";

import { useMemo, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  CheckCircle2,
  XCircle,
  MinusCircle,
  ClipboardList,
} from "lucide-react";
import type {
  RubricDoc,
  RubricCriterion,
  TaskGrade,
  PhaseGrade,
} from "@/utils/skilleval-types";

interface TaskRubricProps {
  rubric: RubricDoc | null;
  grades: TaskGrade[];
  taskName: string;
}

const PHASE_ORDER = ["skill_identification", "module_sequence", "post_processing"];
const PHASE_LABELS: Record<string, string> = {
  skill_identification: "Skill Identification",
  module_sequence: "Module Sequence",
  post_processing: "Post-processing",
  pre_processing: "Pre-processing",
};

type CritVerdict = { verdict: string; evidence_excerpt?: string; note?: string };

function VerdictIcon({ verdict }: { verdict?: string }) {
  if (verdict === "yes") return <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />;
  if (verdict === "no") return <XCircle className="w-4 h-4 text-red-500 shrink-0" />;
  return <MinusCircle className="w-4 h-4 text-muted-foreground/40 shrink-0" />;
}

function CriterionRow({
  c,
  v,
}: {
  c: RubricCriterion;
  v?: CritVerdict;
}) {
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <VerdictIcon verdict={v?.verdict} />
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-mono text-[10px] text-muted-foreground">{c.id}</span>
          {c.criticality && (
            <Badge
              variant="outline"
              className={`text-[10px] px-1.5 py-0 h-4 ${
                c.criticality === "critical"
                  ? "border-amber-400/50 text-amber-600 dark:text-amber-400"
                  : "text-muted-foreground"
              }`}
            >
              {c.criticality}
            </Badge>
          )}
          {c.type && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4">
              {c.type}
            </Badge>
          )}
        </div>
        <p className="text-sm text-foreground/90 whitespace-pre-wrap break-words">{c.criterion}</p>
        {v?.note && (
          <p className="text-xs text-muted-foreground italic">Judge: {v.note}</p>
        )}
        {v?.evidence_excerpt && (
          <pre className="text-[11px] font-mono whitespace-pre-wrap break-words bg-muted/50 rounded-md p-2 border border-border max-h-40 overflow-auto">
            {v.evidence_excerpt}
          </pre>
        )}
      </div>
    </div>
  );
}

export function TaskRubric({ rubric, grades }: TaskRubricProps) {
  // One option per (model, condition) we have grades for.
  const combos = useMemo(() => {
    return grades
      .map((g) => ({
        key: `${g.model}|||${g.condition}`,
        label: `${g.harness} · ${g.model} — ${g.condition}`,
        model: g.model,
        condition: g.condition,
        pct: g.total.pct,
        grade: g,
      }))
      .sort(
        (a, b) =>
          (a.condition === "With Skills" ? -1 : 1) - (b.condition === "With Skills" ? -1 : 1) ||
          b.pct - a.pct,
      );
  }, [grades]);

  const [selectedKey, setSelectedKey] = useState<string>(combos[0]?.key ?? "");
  const selected = combos.find((c) => c.key === selectedKey) ?? combos[0];
  const grade: TaskGrade | undefined = selected?.grade;

  // criterion_id -> verdict, and phase -> grade meta, for the selected run.
  const { verdictMap, phaseMap } = useMemo(() => {
    const verdictMap = new Map<string, CritVerdict>();
    const phaseMap = new Map<string, PhaseGrade>();
    if (grade) {
      for (const ph of grade.phases) {
        phaseMap.set(ph.phase, ph);
        for (const cr of ph.criterion_results) {
          verdictMap.set(cr.criterion_id, {
            verdict: cr.verdict,
            evidence_excerpt: cr.evidence_excerpt,
            note: cr.note,
          });
        }
      }
    }
    return { verdictMap, phaseMap };
  }, [grade]);

  if (!rubric) {
    return (
      <div className="text-center py-16 text-muted-foreground">
        <ClipboardList className="w-12 h-12 mx-auto mb-4 opacity-40" />
        <p>No rubric available for this task.</p>
      </div>
    );
  }

  const phaseKeys = [
    ...PHASE_ORDER.filter((p) => rubric.phases[p]),
    ...Object.keys(rubric.phases).filter((p) => !PHASE_ORDER.includes(p)),
  ];

  const moduleCritical = new Map<number, boolean>();
  const ms = phaseMap.get("module_sequence");
  if (ms?.critical_passed_per_step) {
    for (const s of ms.critical_passed_per_step) moduleCritical.set(s.module_id, s.critical_passed);
  }

  return (
    <div className="space-y-5">
      <p className="text-sm text-muted-foreground">
        Tasks are graded by an LLM judge against this rubric (no Python verifier). Pick a model to
        see its per-criterion verdicts.
      </p>

      {/* Run picker + score summary */}
      {combos.length > 0 && (
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">Graded run</label>
            <select
              value={selectedKey}
              onChange={(e) => setSelectedKey(e.target.value)}
              className="block rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {combos.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label} ({c.pct.toFixed(0)}%)
                </option>
              ))}
            </select>
          </div>
          {grade && (
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="default" className="font-mono">
                Total {grade.total.pct.toFixed(1)}% ({grade.total.score}/{grade.total.max})
              </Badge>
              {grade.phases.map((ph) => (
                <Badge key={ph.phase} variant="outline" className="font-mono text-xs gap-1">
                  {PHASE_LABELS[ph.phase] ?? ph.phase}: {ph.score}/{ph.max_score}
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Phases */}
      {phaseKeys.map((pk) => {
        const phase = rubric.phases[pk];
        const flatCriteria = phase.criteria ?? [];
        const steps = phase.steps ?? [];
        if (flatCriteria.length === 0 && steps.length === 0) return null;
        const ph = phaseMap.get(pk);

        return (
          <Card key={pk} className="overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-border bg-muted/30">
              <span className="text-sm font-semibold text-foreground">
                {PHASE_LABELS[pk] ?? pk}
              </span>
              {ph && (
                <Badge variant="outline" className="font-mono text-xs">
                  {ph.score}/{ph.max_score}
                  {ph.critical_passed === false && (
                    <span className="ml-1 text-red-500">· critical failed</span>
                  )}
                </Badge>
              )}
            </div>

            {/* Flat criteria (skill_identification, post_processing) */}
            {flatCriteria.length > 0 && (
              <div className="divide-y divide-border">
                {flatCriteria.map((c) => (
                  <CriterionRow key={c.id} c={c} v={verdictMap.get(c.id)} />
                ))}
              </div>
            )}

            {/* Module steps (module_sequence) */}
            {steps.map((step) => {
              const crit = moduleCritical.get(step.module_id);
              return (
                <div key={step.module_id} className="border-t border-border first:border-t-0">
                  <div className="flex items-center justify-between px-4 py-2 bg-muted/10">
                    <span className="text-sm font-medium text-foreground">
                      <span className="text-muted-foreground font-mono text-xs mr-2">
                        M{step.module_id}
                      </span>
                      {step.module_name}
                    </span>
                    {crit !== undefined && (
                      <Badge
                        variant="outline"
                        className={`text-[10px] ${
                          crit ? "text-emerald-600 dark:text-emerald-400" : "text-red-500"
                        }`}
                      >
                        {crit ? "critical passed" : "critical failed"}
                      </Badge>
                    )}
                  </div>
                  {step.module_description && (
                    <p className="px-4 pt-2 text-xs text-muted-foreground">
                      {step.module_description}
                    </p>
                  )}
                  <div className="divide-y divide-border">
                    {step.criteria.map((c) => (
                      <CriterionRow key={c.id} c={c} v={verdictMap.get(c.id)} />
                    ))}
                  </div>
                </div>
              );
            })}
          </Card>
        );
      })}
    </div>
  );
}
