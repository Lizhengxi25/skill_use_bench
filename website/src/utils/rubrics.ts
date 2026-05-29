import rubricsRegistry from "../data/rubrics-registry.json";
import type { RubricDoc } from "./skilleval-types";

export function getRubric(taskKey: string): RubricDoc | null {
  return (rubricsRegistry as unknown as Record<string, RubricDoc>)[taskKey] ?? null;
}
