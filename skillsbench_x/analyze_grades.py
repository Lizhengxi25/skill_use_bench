#!/usr/bin/env python3
"""Analyze judge grades for one LLM-agent trajectory run.

Usage:
    python3 analyze_grades.py <grades_dir> [--json OUT.json] [--md OUT.md] \
                              [--per-task] [--per-task-sort {task,norm}]

<grades_dir> is a path like lave_minimax/grades/20260520T122019Z-47193/

Reads judge_phase_*.json files under <grades_dir>/<task_id>/ and prints a
friendly summary report. Pass --json to also dump the summary as JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Optional

PHASES = ("module_sequence", "post_processing", "skill_identification")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_phase(task_dir: Path, phase: str) -> Optional[dict]:
    """Return the parsed JSON for one phase, or None if missing/unreadable."""
    fp = task_dir / f"judge_phase_{phase}.json"
    if not fp.exists() or fp.stat().st_size == 0:
        return None
    try:
        with fp.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def load_grades_dir(grades_dir: Path) -> dict:
    """Load every task's grades from a single run's grades dir.

    The agent identity is derived from the path itself:
        agent     = grades_dir.parent.parent.name   (e.g. "lave_minimax")
        timestamp = grades_dir.name                 (e.g. "20260520T122019Z-47193")
    """
    agent = grades_dir.parent.parent.name
    label = agent
    timestamp = grades_dir.name

    if not grades_dir.is_dir():
        return {
            "agent": agent,
            "label": label,
            "timestamp": timestamp,
            "grades_dir": str(grades_dir),
            "task_ids": [],
            "per_task": [],
            "ungraded_ids": [],
            "error": f"grades dir not found: {grades_dir}",
        }

    task_ids = sorted(
        int(p.name) for p in grades_dir.iterdir()
        if p.is_dir() and p.name.isdigit()
    )

    per_task = []
    ungraded_ids = []
    for tid in task_ids:
        tdir = grades_dir / str(tid)
        phase_data = {ph: _load_phase(tdir, ph) for ph in PHASES}
        if any(v is None for v in phase_data.values()):
            ungraded_ids.append(tid)
            continue

        phases = {}
        total_score = 0
        total_max = 0
        crit_count = 0
        for ph, d in phase_data.items():
            s = int(d.get("score", 0))
            m = int(d.get("max_score", 0))
            cp = bool(d.get("critical_passed", False))
            phases[ph] = {
                "score": s,
                "max_score": m,
                "norm": (s / m) if m > 0 else 0.0,
                "critical_passed": cp,
            }
            total_score += s
            total_max += m
            if cp:
                crit_count += 1

        # Gated scoring: if skill_identification is not at full score, zero out
        # the other phases. Tasks with skill_id.max_score == 0 are treated as
        # "full" so a degenerate rubric does not unfairly zero the task.
        si = phases["skill_identification"]
        si_full = (si["max_score"] == 0) or (si["score"] == si["max_score"])
        if si_full:
            gated_total_score = total_score
        else:
            gated_total_score = si["score"]
        gated_total_norm = (gated_total_score / total_max) if total_max > 0 else 0.0

        per_task.append({
            "task_id": tid,
            "phases": phases,
            "total_score": total_score,
            "total_max": total_max,
            "total_norm": (total_score / total_max) if total_max > 0 else 0.0,
            "critical_passed_count": crit_count,
            "all_critical_passed": crit_count == len(PHASES),
            "skill_id_full": si_full,
            "gated_total_score": gated_total_score,
            "gated_total_norm": gated_total_norm,
        })

    return {
        "agent": agent,
        "label": label,
        "timestamp": timestamp,
        "grades_dir": str(grades_dir),
        "task_ids": task_ids,
        "per_task": per_task,
        "ungraded_ids": ungraded_ids,
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None, "stdev": None}
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def summarize(loaded: dict) -> dict:
    per_task = loaded["per_task"]
    n_graded = len(per_task)
    n_total = len(loaded["task_ids"])

    summary = {
        "agent": loaded["agent"],
        "label": loaded["label"],
        "timestamp": loaded["timestamp"],
        "n_total": n_total,
        "n_graded": n_graded,
        "n_ungraded": n_total - n_graded,
        "ungraded_ids": loaded["ungraded_ids"],
    }

    if n_graded == 0:
        return summary

    raw_totals = [t["total_score"] for t in per_task]
    rankable = [t for t in per_task if t["total_max"] > 0]
    norm_totals = [t["total_norm"] for t in rankable]
    degenerate_ids = [t["task_id"] for t in per_task if t["total_max"] == 0]

    summary["raw_total"] = _stats(raw_totals)
    summary["norm_total"] = _stats(norm_totals)
    summary["degenerate_ids"] = degenerate_ids
    summary["n_rankable"] = len(rankable)

    gated_raw = [t["gated_total_score"] for t in per_task]
    gated_norm = [t["gated_total_norm"] for t in rankable]
    summary["gated_raw_total"] = _stats(gated_raw)
    summary["gated_norm_total"] = _stats(gated_norm)
    summary["skill_id_full_count"] = sum(1 for t in per_task if t["skill_id_full"])
    summary["skill_id_full_rate"] = summary["skill_id_full_count"] / n_graded

    # Skill-used subset: tasks where the agent actually earned full score in
    # skill_identification (rubric must be non-degenerate, i.e. max_score > 0).
    # Among those tasks, what is the normalized total score across all phases?
    skill_used = [
        t for t in rankable
        if t["phases"]["skill_identification"]["max_score"] > 0
        and t["skill_id_full"]
    ]
    summary["skill_used_ids"] = [t["task_id"] for t in skill_used]
    summary["skill_used_count"] = len(skill_used)
    summary["skill_used_rate"] = (
        len(skill_used) / summary["n_rankable"]
    ) if summary["n_rankable"] else 0.0
    summary["skill_used_norm_total"] = _stats([t["total_norm"] for t in skill_used])
    summary["skill_used_raw_total"] = _stats([t["total_score"] for t in skill_used])

    per_phase = {}
    for ph in PHASES:
        raws = [t["phases"][ph]["score"] for t in per_task]
        norms = [t["phases"][ph]["norm"] for t in per_task]
        crit_passes = sum(1 for t in per_task if t["phases"][ph]["critical_passed"])
        per_phase[ph] = {
            "mean_raw": statistics.fmean(raws),
            "mean_norm": statistics.fmean(norms),
            "crit_pass_count": crit_passes,
            "crit_pass_rate": crit_passes / n_graded,
        }
    summary["per_phase"] = per_phase

    all_crit = sum(1 for t in per_task if t["all_critical_passed"])
    summary["all_critical_passed_count"] = all_crit
    summary["all_critical_passed_rate"] = all_crit / n_graded

    ranked = sorted(rankable, key=lambda t: t["total_norm"], reverse=True)
    summary["top5"] = [
        {
            "task_id": t["task_id"],
            "total_score": t["total_score"],
            "total_max": t["total_max"],
            "total_norm": t["total_norm"],
            "critical_passed_count": t["critical_passed_count"],
        }
        for t in ranked[:5]
    ]
    summary["bottom5"] = [
        {
            "task_id": t["task_id"],
            "total_score": t["total_score"],
            "total_max": t["total_max"],
            "total_norm": t["total_norm"],
            "critical_passed_count": t["critical_passed_count"],
        }
        for t in ranked[-5:][::-1]
    ]

    bins = [0] * 10  # 0.0-0.1, ..., 0.9-1.0; 1.0 lands in last bin
    for t in rankable:
        idx = min(int(t["total_norm"] * 10), 9)
        bins[idx] += 1
    summary["histogram"] = bins

    # Compact per-task rows: normalized score for each phase plus total.
    summary["per_task"] = [
        {
            "task_id": t["task_id"],
            "module_sequence_norm": t["phases"]["module_sequence"]["norm"],
            "post_processing_norm": t["phases"]["post_processing"]["norm"],
            "skill_identification_norm": t["phases"]["skill_identification"]["norm"],
            "total_score": t["total_score"],
            "total_max": t["total_max"],
            "total_norm": t["total_norm"],
            "critical_passed_count": t["critical_passed_count"],
            "degenerate": t["total_max"] == 0,
            "skill_id_full": t["skill_id_full"],
            "gated_total_score": t["gated_total_score"],
            "gated_total_norm": t["gated_total_norm"],
        }
        for t in per_task
    ]

    return summary


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------

def _hr(char: str = "=", width: int = 72) -> str:
    return char * width


def print_per_task(summary: dict, out=sys.stdout, sort_by: str = "task") -> None:
    """Print the full per-task normalized-score table.

    sort_by: 'task' (ascending task_id) or 'norm' (descending total_norm).
    """
    p = lambda s="": print(s, file=out)
    rows = list(summary.get("per_task", []))
    if not rows:
        return
    if sort_by == "norm":
        rows.sort(key=lambda r: r["total_norm"], reverse=True)
    else:
        rows.sort(key=lambda r: r["task_id"])

    p("")
    p(f"Per-task normalized scores ({len(rows)} tasks, sorted by {sort_by})")
    header = (
        f"  {'task':>4}  {'mod_seq':>7}  {'post_pr':>7}  {'skill_id':>8}  "
        f"{'total':>7}  {'gated':>7}  {'raw':>9}  {'crit':>4}"
    )
    p(header)
    p("  " + "-" * (len(header) - 2))
    for r in rows:
        deg_mark = "  (deg)" if r["degenerate"] else ""
        p(
            f"  {r['task_id']:>4}  "
            f"{r['module_sequence_norm']:>7.3f}  "
            f"{r['post_processing_norm']:>7.3f}  "
            f"{r['skill_identification_norm']:>8.3f}  "
            f"{r['total_norm']:>7.3f}  "
            f"{r['gated_total_norm']:>7.3f}  "
            f"{r['total_score']:>3}/{r['total_max']:<3}  "
            f"{r['critical_passed_count']:>1}/3{deg_mark}"
        )
    p("")


def print_report(summary: dict, out=sys.stdout) -> None:
    p = lambda s="": print(s, file=out)
    p(_hr("="))
    p(f" Agent: {summary['label']}")
    p(f" Run:   {summary['timestamp']}")
    p(_hr("="))

    n_total = summary["n_total"]
    n_graded = summary["n_graded"]
    n_ung = summary["n_ungraded"]
    p(f"Tasks graded: {n_graded} / {n_total}    ({n_ung} ungraded)")
    if summary.get("degenerate_ids"):
        deg = summary["degenerate_ids"]
        p(f"Degenerate tasks (all max_score=0, excluded from rankings/histogram): "
          f"{len(deg)}  -> ids: {deg}")

    if n_graded == 0:
        p("")
        p("(No graded tasks for this agent yet. Skipping numeric report.)")
        p("")
        return

    rt = summary["raw_total"]
    nt = summary["norm_total"]
    p("")
    p("Overall totals (sum across 3 phases per task)")
    p(f"  raw total   mean={rt['mean']:.2f}  median={rt['median']:.1f}  "
      f"min={rt['min']}  max={rt['max']}  stdev={rt['stdev']:.2f}")
    p(f"  normalized  mean={nt['mean']:.3f} median={nt['median']:.3f} "
      f"min={nt['min']:.3f} max={nt['max']:.3f} stdev={nt['stdev']:.3f}")

    p("")
    p("Per-phase summary")
    p(f"  {'phase':<22} {'mean_raw':>9} {'mean_norm':>10} {'crit_pass_rate':>16}")
    for ph in PHASES:
        s = summary["per_phase"][ph]
        rate_str = f"{s['crit_pass_rate']*100:5.1f}% ({s['crit_pass_count']}/{n_graded})"
        p(f"  {ph:<22} {s['mean_raw']:>9.2f} {s['mean_norm']:>10.3f} {rate_str:>16}")

    ac = summary["all_critical_passed_count"]
    p("")
    p(f"All-three-phases critical-passed: "
      f"{summary['all_critical_passed_rate']*100:.1f}%  ({ac} / {n_graded})")

    grt = summary["gated_raw_total"]
    gnt = summary["gated_norm_total"]
    si_full = summary["skill_id_full_count"]
    p("")
    p("Gated scoring (skill_identification must be full; else other phases -> 0)")
    p(f"  skill_id full: {summary['skill_id_full_rate']*100:.1f}% ({si_full}/{n_graded})")
    p(f"  gated raw    mean={grt['mean']:.2f}  median={grt['median']:.1f}  "
      f"min={grt['min']}  max={grt['max']}  stdev={grt['stdev']:.2f}")
    p(f"  gated norm   mean={gnt['mean']:.3f} median={gnt['median']:.3f} "
      f"min={gnt['min']:.3f} max={gnt['max']:.3f} stdev={gnt['stdev']:.3f}")

    sun = summary["skill_used_norm_total"]
    sur = summary["skill_used_raw_total"]
    su_n = summary["skill_used_count"]
    p("")
    p("Skill-used subset (tasks with full score on skill_identification, max>0)")
    p(f"  count: {su_n} / {summary['n_rankable']} rankable "
      f"({summary['skill_used_rate']*100:.1f}%)")
    if su_n > 0:
        p(f"  raw total    mean={sur['mean']:.2f}  median={sur['median']:.1f}  "
          f"min={sur['min']}  max={sur['max']}  stdev={sur['stdev']:.2f}")
        p(f"  normalized   mean={sun['mean']:.3f} median={sun['median']:.3f} "
          f"min={sun['min']:.3f} max={sun['max']:.3f} stdev={sun['stdev']:.3f}")
    else:
        p("  (no tasks where the agent earned full skill_identification score)")

    p("")
    p("Top 5 tasks by normalized total")
    for i, t in enumerate(summary["top5"], 1):
        p(f"  #{i}  task={t['task_id']:<3}  "
          f"total={t['total_score']}/{t['total_max']}  "
          f"norm={t['total_norm']:.3f}  crit={t['critical_passed_count']}/3")

    p("")
    p("Bottom 5 tasks by normalized total")
    for i, t in enumerate(summary["bottom5"], 1):
        p(f"  #{i}  task={t['task_id']:<3}  "
          f"total={t['total_score']}/{t['total_max']}  "
          f"norm={t['total_norm']:.3f}  crit={t['critical_passed_count']}/3")

    p("")
    p("Normalized-total distribution (10% bins)")
    bins = summary["histogram"]
    max_count = max(bins) if any(bins) else 1
    bar_width = 30
    for i, count in enumerate(bins):
        lo = i / 10
        hi = (i + 1) / 10
        label = f"[{lo:.1f},{hi:.1f}{']' if i == 9 else ')'}"
        bar_len = int(round(count / max_count * bar_width)) if max_count else 0
        bar = "#" * bar_len
        p(f"  {label:<12} {bar:<{bar_width}} {count}")
    p("")


def _md_section(summary: dict, sort_by: str = "task") -> str:
    """Render one agent's overall summary + per-task table as a markdown string."""
    lines: list[str] = []
    lines.append(f"# Grade report: {summary['label']}")
    lines.append("")
    lines.append(f"- Run: `{summary['timestamp']}`")
    lines.append(
        f"- Tasks graded: **{summary['n_graded']} / {summary['n_total']}** "
        f"({summary['n_ungraded']} ungraded)"
    )
    if summary.get("degenerate_ids"):
        deg = summary["degenerate_ids"]
        lines.append(
            f"- Degenerate tasks (all `max_score=0`, excluded from rankings/histogram): "
            f"{len(deg)} -> {deg}"
        )

    if summary["n_graded"] == 0:
        lines.append("")
        lines.append("_No graded tasks for this agent yet._")
        lines.append("")
        return "\n".join(lines)

    rt = summary["raw_total"]
    nt = summary["norm_total"]
    lines.append("")
    lines.append("## Overall results")
    lines.append("")
    lines.append(
        f"- Raw total per task: mean **{rt['mean']:.2f}**, "
        f"median {rt['median']:.1f}, min {rt['min']}, max {rt['max']}, "
        f"stdev {rt['stdev']:.2f}"
    )
    lines.append(
        f"- Normalized total per task: mean **{nt['mean']:.3f}**, "
        f"median {nt['median']:.3f}, min {nt['min']:.3f}, max {nt['max']:.3f}, "
        f"stdev {nt['stdev']:.3f}"
    )
    pp = summary["per_phase"]
    phase_bits = [
        f"`{ph}` mean_norm={pp[ph]['mean_norm']:.3f} "
        f"(crit pass {pp[ph]['crit_pass_rate']*100:.1f}%)"
        for ph in PHASES
    ]
    lines.append("- Per-phase: " + "; ".join(phase_bits))
    ac = summary["all_critical_passed_count"]
    lines.append(
        f"- All-three-phases critical-passed: "
        f"**{summary['all_critical_passed_rate']*100:.1f}%** ({ac} / {summary['n_graded']})"
    )
    grt = summary["gated_raw_total"]
    gnt = summary["gated_norm_total"]
    lines.append(
        f"- Gated scoring (skill_identification gate): "
        f"skill_id full **{summary['skill_id_full_rate']*100:.1f}%** "
        f"({summary['skill_id_full_count']} / {summary['n_graded']}); "
        f"gated raw mean **{grt['mean']:.2f}**, "
        f"gated norm mean **{gnt['mean']:.3f}**"
    )
    sun = summary["skill_used_norm_total"]
    su_n = summary["skill_used_count"]
    if su_n > 0:
        lines.append(
            f"- Skill-used subset (full skill_identification score, max>0): "
            f"**{su_n} / {summary['n_rankable']}** "
            f"({summary['skill_used_rate']*100:.1f}%); "
            f"normalized total mean **{sun['mean']:.3f}**, "
            f"median {sun['median']:.3f}, min {sun['min']:.3f}, max {sun['max']:.3f}"
        )
    else:
        lines.append(
            "- Skill-used subset (full skill_identification score, max>0): **0 tasks**"
        )

    rows = list(summary.get("per_task", []))
    if sort_by == "norm":
        rows.sort(key=lambda r: r["total_norm"], reverse=True)
    else:
        rows.sort(key=lambda r: r["task_id"])

    lines.append("")
    lines.append(f"## Per-task normalized scores ({len(rows)} tasks, sorted by {sort_by})")
    lines.append("")
    lines.append(
        "| task | mod_seq | post_proc | skill_id | total_norm | gated_norm | raw | crit | notes |"
    )
    lines.append(
        "|---:|---:|---:|---:|---:|---:|:---:|:---:|:---|"
    )
    for r in rows:
        notes_bits = []
        if r["degenerate"]:
            notes_bits.append("degenerate (max=0)")
        if not r["skill_id_full"]:
            notes_bits.append("skill_id<full -> gated")
        note = "; ".join(notes_bits)
        lines.append(
            f"| {r['task_id']} "
            f"| {r['module_sequence_norm']:.3f} "
            f"| {r['post_processing_norm']:.3f} "
            f"| {r['skill_identification_norm']:.3f} "
            f"| {r['total_norm']:.3f} "
            f"| {r['gated_total_norm']:.3f} "
            f"| {r['total_score']}/{r['total_max']} "
            f"| {r['critical_passed_count']}/3 "
            f"| {note} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_markdown(summaries: list[dict], path: str, sort_by: str = "task") -> None:
    """Write per-task markdown report(s) to a single file. Multi-agent runs
    are concatenated with horizontal-rule separators."""
    sections = [_md_section(s, sort_by=sort_by) for s in summaries]
    content = "\n\n---\n\n".join(sections).rstrip() + "\n"
    with open(path, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize judge grades for one LLM-agent trajectory run.",
    )
    parser.add_argument(
        "grades_dir",
        help="Path to a grades run dir, e.g. lave_minimax/grades/20260520T122019Z-47193/",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        metavar="PATH",
        help="Also write the summary to this path as JSON.",
    )
    parser.add_argument(
        "--per-task",
        dest="per_task",
        action="store_true",
        help="Also print the full per-task normalized-score table.",
    )
    parser.add_argument(
        "--per-task-sort",
        choices=["task", "norm"],
        default="task",
        help="Sort key for --per-task and --md tables (default: task id ascending).",
    )
    parser.add_argument(
        "--md",
        dest="md_out",
        metavar="PATH",
        help="Write a markdown report with brief overall results and a per-task table.",
    )
    args = parser.parse_args(argv)

    grades_dir = Path(args.grades_dir).resolve()
    loaded = load_grades_dir(grades_dir)
    summary = summarize(loaded)

    print_report(summary)
    if args.per_task:
        print_per_task(summary, sort_by=args.per_task_sort)

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"Wrote JSON summary to {args.json_out}")

    if args.md_out:
        write_markdown([summary], args.md_out, sort_by=args.per_task_sort)
        print(f"Wrote markdown report to {args.md_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
