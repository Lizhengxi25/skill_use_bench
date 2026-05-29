"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Leaderboard } from "@/components/Leaderboard";
import {
  leaderboardData,
  SORT_OPTIONS,
  type SortKey,
} from "@/data/leaderboard-data";

export function LeaderboardClient() {
  const [sortKey, setSortKey] = useState<SortKey>("with_skills");

  return (
    <div className="flex flex-col min-h-screen">
      {/* Page header */}
      <div className="max-w-6xl mx-auto px-4 md:px-8 pt-24 md:pt-28 w-full">
        <Link
          href="/"
          className="text-muted-foreground text-sm hover:text-foreground transition-colors flex items-center gap-2 mb-6 group w-fit"
        >
          <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" />
          Back to Home
        </Link>
        <h1 className="text-3xl font-bold tracking-tight mb-4">Agent Leaderboard</h1>
        <p className="text-muted-foreground text-lg max-w-2xl">
          Skill-eval scores across {leaderboardData.length} model configurations on{" "}
          {leaderboardData[0]?.tasks ?? 0} tasks. Each task is scored by an LLM judge and
          micro-averaged across its grading phases.
        </p>
        <p className="text-muted-foreground text-sm max-w-2xl mt-3">
          Error bars are 95% task-level confidence intervals. Each reasoning effort is treated as a
          separate model, compared with vs. without skills.
        </p>
      </div>

      {/* Sort controls */}
      <div className="max-w-6xl mx-auto px-4 md:px-8 pt-6 pb-4 w-full">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground text-sm mr-1">Sort by</span>
          {SORT_OPTIONS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setSortKey(key)}
              className={`px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${
                sortKey === key
                  ? "bg-foreground text-background"
                  : "bg-muted text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Full-width leaderboard panel */}
      <div className="max-w-6xl mx-auto px-4 md:px-8 w-full flex-1 pb-12">
        <div className="border border-border overflow-hidden">
          <Leaderboard compact sortKey={sortKey} />
        </div>
      </div>
    </div>
  );
}
