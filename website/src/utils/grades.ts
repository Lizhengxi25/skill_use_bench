import gradesRegistry from "../data/grades-registry.json";
import type { TaskGrade } from "./skilleval-types";

export function getTaskGrades(taskKey: string): TaskGrade[] {
  return (gradesRegistry as unknown as TaskGrade[]).filter((g) => g.taskKey === taskKey);
}

export function getAllGrades(): TaskGrade[] {
  return gradesRegistry as unknown as TaskGrade[];
}
