// VegaNotes token registry (TypeScript). Mirrors backend/app/parser/tokens.py.
// Adding a new token: add entry here AND in tokens.py, then add a fixture.

import { parseEta, parseDuration, parsePriorityRank } from "./time.ts";

export type Normalize = (v: string) => string | number | null;

export interface TokenSpec {
  name: string;
  multi: boolean;
  normalize?: Normalize;
}

export const REGISTRY: Record<string, TokenSpec> = {
  task:     { name: "task",     multi: true },
  eta:      { name: "eta",      multi: false, normalize: (v) => parseEta(v) },
  priority: { name: "priority", multi: false, normalize: (v) => parsePriorityRank(v) },
  project:  { name: "project",  multi: true },
  owner:    { name: "owner",    multi: true },
  status:   { name: "status",   multi: false },
  estimate: { name: "estimate", multi: false, normalize: (v) => parseDuration(v) },
  feature:  { name: "feature",  multi: true },
  link:     { name: "link",     multi: true },
};

export const isKnown = (name: string): boolean => name in REGISTRY;
