// VegaNotes token registry (TypeScript). Mirrors backend/app/parser/tokens.py.
// Adding a new token: add entry here AND in tokens.py, then add a fixture.

import { parseEta, parseDuration, parsePriorityRank } from "./time.ts";

export type Normalize = (v: string) => string | number | null;

export interface TokenSpec {
  name: string;
  multi: boolean;
  normalize?: Normalize;
}

const STATUS_ALIASES: Record<string, string> = {
  "in progress": "in-progress",
  "in_progress": "in-progress",
  "inprogress": "in-progress",
  "wip": "in-progress",
  "doing": "in-progress",
  "working": "in-progress",
  "complete": "done",
  "completed": "done",
  "finished": "done",
  "closed": "done",
  "open": "todo",
  "pending": "todo",
  "to-do": "todo",
  "todo": "todo",
  "block": "blocked",
  "blocked": "blocked",
  "stuck": "blocked",
};

export function normalizeStatus(value: string): string {
  if (!value) return "todo";
  const v = value.trim().toLowerCase();
  return STATUS_ALIASES[v] ?? v;
}

export const REGISTRY: Record<string, TokenSpec> = {
  task:     { name: "task",     multi: true },
  id:       { name: "id",       multi: false },
  eta:      { name: "eta",      multi: false, normalize: (v) => parseEta(v) },
  priority: { name: "priority", multi: false, normalize: (v) => parsePriorityRank(v) },
  project:  { name: "project",  multi: true },
  owner:    { name: "owner",    multi: true },
  status:   { name: "status",   multi: false, normalize: (v) => normalizeStatus(v) },
  estimate: { name: "estimate", multi: false, normalize: (v) => parseDuration(v) },
  feature:  { name: "feature",  multi: true },
  link:     { name: "link",     multi: true },
};

export const isKnown = (name: string): boolean => name in REGISTRY;
