"use client";

import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { TaskDemo } from "./task-demo";

import { TaskRubric } from "./task-rubric";
import { TaskResults } from "./task-results";
import { TrajectoryViewer } from "./trajectory-viewer";
import { BookOpen, ClipboardCheck, Trophy, Play } from "lucide-react";
import type { Task } from "@/utils/tasks";
import type { TaskResult, TrajectoryIndexEntry } from "@/utils/trajectory-types";
import type { RubricDoc, TaskGrade } from "@/utils/skilleval-types";

interface TaskTabsProps {
  task: Task;
  instructionContent: React.ReactNode;
  rubric: RubricDoc | null;
  grades: TaskGrade[];
  results: TaskResult[];
  trajectoryIndex: TrajectoryIndexEntry[];
}

const triggerClass =
  "rounded-none border-0 border-b-2 border-transparent data-[state=active]:border-b-foreground data-[state=active]:bg-transparent data-[state=active]:shadow-none px-4 py-2.5 text-sm font-medium text-muted-foreground data-[state=active]:text-foreground gap-2";

export function TaskTabs({
  task,
  instructionContent,
  rubric,
  grades,
  results,
  trajectoryIndex,
}: TaskTabsProps) {
  return (
    <Tabs defaultValue="instruction" className="w-full">
      <TabsList className="w-full justify-start bg-transparent border-b border-border rounded-none h-auto p-0 gap-0">
        <TabsTrigger value="instruction" className={triggerClass}>
          <BookOpen className="w-4 h-4" />
          Instruction
        </TabsTrigger>
        <TabsTrigger value="rubric" className={triggerClass}>
          <ClipboardCheck className="w-4 h-4" />
          Rubric &amp; Grading
        </TabsTrigger>
        <TabsTrigger value="results" className={triggerClass}>
          <Trophy className="w-4 h-4" />
          Results
        </TabsTrigger>
        <TabsTrigger value="trajectory" className={triggerClass}>
          <Play className="w-4 h-4" />
          Trajectory
        </TabsTrigger>
      </TabsList>

      <TabsContent value="instruction" className="mt-8">
        {task.demo_url && (
          <div className="mb-12">
            <TaskDemo demoUrl={task.demo_url} />
          </div>
        )}
        <article>{instructionContent}</article>
      </TabsContent>

      <TabsContent value="rubric" className="mt-8">
        <TaskRubric rubric={rubric} grades={grades} taskName={task.title} />
      </TabsContent>

      <TabsContent value="results" className="mt-8">
        <TaskResults results={results} taskName={task.title} />
      </TabsContent>

      <TabsContent value="trajectory" className="mt-8">
        <TrajectoryViewer trajectoryIndex={trajectoryIndex} taskName={task.title} />
      </TabsContent>
    </Tabs>
  );
}
