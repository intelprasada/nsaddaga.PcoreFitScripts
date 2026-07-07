// Mini-DSL for the FilterBar chip input. Mirrors `tools/vn/vn/query.py` —
// keep the two in sync. Operators: =, !=, >=, <=, >, <, in, not in, like,
// exists, nexists.
// `@key` prefix routes to the generic TaskAttr filter; bare keys map to
// the existing fixed columns (owner/project/feature/priority/status/eta).
// Bare `#tag` (single word) is sugar for `@tag exists` — matches any task
// carrying that bare hashtag as a presence attribute.

export type OpCode =
  | "eq" | "ne" | "in" | "nin"
  | "gte" | "lte" | "gt" | "lt" | "like"
  | "exists" | "nexists";

export interface Clause {
  /** Original LHS as the user typed it, lowercased. ``@`` prefix preserved. */
  lhs: string;
  op: OpCode;
  /** Right-hand side, untrimmed-internal but stripped on the edges. */
  value: string;
  /** True if the LHS started with `@` (i.e. arbitrary TaskAttr key). */
  isAttr: boolean;
}

export class DSLError extends Error {
  constructor(msg: string) { super(msg); this.name = "DSLError"; }
}

const SYM_OPS: Array<[string, OpCode]> = [
  [">=", "gte"], ["<=", "lte"], ["!=", "ne"],
  ["=",  "eq"],  [">",  "gt"],  ["<",  "lt"],
];

// Word ops — longest first so "not in" matches before "in".
const WORD_OPS: Array<[string, OpCode]> = [
  ["not in", "nin"], ["in", "in"], ["like", "like"],
];

// Unary word ops — no RHS after the op keyword.
const UNARY_WORD_OPS: Array<[string, OpCode]> = [
  ["nexists", "nexists"], ["exists", "exists"],
];

const FIXED_COLUMNS = new Set(["owner", "project", "feature", "priority", "status"]);
const FIXED_OPS = new Set<OpCode>(["eq", "ne", "in"]);

function normalizeLhs(raw: string): { lhs: string; isAttr: boolean } {
  const t = raw.trim();
  if (t.startsWith("@")) return { lhs: "@" + t.slice(1).trim().toLowerCase(), isAttr: true };
  return { lhs: t.toLowerCase(), isAttr: false };
}

export function parseClause(input: string): Clause {
  const s = input.trim();
  if (!s) throw new DSLError("empty clause");

  // Sugar: bare `#tag` → `@tag exists`. Matches a single hashtag word with
  // no operator or value after it.
  const bareHash = /^#([a-zA-Z][\w-]*)$/.exec(s);
  if (bareHash) {
    return {
      lhs: "@" + bareHash[1].toLowerCase(),
      isAttr: true,
      op: "exists",
      value: "",
    };
  }

  // Unary word ops: `@key exists`, `@key nexists`.
  for (const [word, code] of UNARY_WORD_OPS) {
    const re = new RegExp(`^(.+?)\\s+${word}\\s*$`, "i");
    const m = re.exec(s);
    if (m) {
      const lhs = m[1].trim();
      if (!lhs) throw new DSLError(`malformed clause: ${input}`);
      const norm = normalizeLhs(lhs);
      if (!norm.isAttr) {
        throw new DSLError(
          `${word} requires an @attr key (got "${lhs}"); use @${lhs} ${word}`,
        );
      }
      return { lhs: norm.lhs, isAttr: norm.isAttr, op: code, value: "" };
    }
  }

  for (const [word, code] of WORD_OPS) {
    const re = new RegExp(`^(.+?)\\s+${word.replace(/ /g, "\\s+")}\\s+(.+)$`, "i");
    const m = re.exec(s);
    if (m) {
      const lhs = m[1].trim();
      const rhs = m[2].trim();
      if (!lhs || !rhs) throw new DSLError(`malformed clause: ${input}`);
      const norm = normalizeLhs(lhs);
      return { lhs: norm.lhs, isAttr: norm.isAttr, op: code, value: rhs };
    }
  }

  for (const [tok, code] of SYM_OPS) {
    const idx = s.indexOf(tok);
    if (idx <= 0) continue;
    const lhs = s.slice(0, idx).trim();
    const rhs = s.slice(idx + tok.length).trim();
    if (!lhs) throw new DSLError(`missing key in ${input}`);
    if (!rhs) throw new DSLError(`missing value in ${input}`);
    const norm = normalizeLhs(lhs);
    return { lhs: norm.lhs, isAttr: norm.isAttr, op: code, value: rhs };
  }

  throw new DSLError(
    `no operator found in "${input}"; expected one of =, !=, >=, <=, >, <, ` +
    `'in', 'not in', 'exists', 'nexists' (or bare #tag)`,
  );
}

/** Compile a list of clauses to ``[paramName, value]`` pairs. */
export function compileClauses(clauses: string[]): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  for (const raw of clauses) {
    if (!raw || !raw.trim()) continue;
    const c = parseClause(raw);
    out.push(...compileOne(c, raw));
  }
  return out;
}

function compileOne(c: Clause, raw: string): Array<[string, string]> {
  if (c.isAttr) {
    const key = c.lhs.slice(1);
    if (!key) throw new DSLError(`empty attr key in ${raw}`);
    if (c.op === "exists" || c.op === "nexists") {
      return [["attr", `${key}:${c.op}:`]];
    }
    return [["attr", `${key}:${c.op}:${c.value}`]];
  }
  if (c.op === "exists" || c.op === "nexists") {
    throw new DSLError(`${c.op} requires an @attr key (got "${c.lhs}")`);
  }
  if (c.lhs === "q") {
    if (c.op !== "eq") throw new DSLError(`q only supports '=' (got ${c.op})`);
    return [["q", c.value]];
  }
  if (c.lhs === "kind") {
    if (c.op !== "eq") throw new DSLError(`kind only supports '=' (got ${c.op})`);
    return [["kind", c.value]];
  }
  if (c.lhs === "eta") return compileEta(c, raw);
  if (FIXED_COLUMNS.has(c.lhs)) {
    if (!FIXED_OPS.has(c.op)) {
      throw new DSLError(
        `operator ${c.op} not supported on ${c.lhs}; ` +
        `use =, !=, in (or @${c.lhs} for richer ops)`,
      );
    }
    if (c.op === "ne") return [[`not_${c.lhs}`, c.value]];
    return [[c.lhs, c.value]];
  }
  throw new DSLError(
    `unknown key "${c.lhs}"; prefix arbitrary attrs with '@' (e.g. @${c.lhs}=…)`,
  );
}

function compileEta(c: Clause, raw: string): Array<[string, string]> {
  switch (c.op) {
    case "gte": return [["eta_after", c.value]];
    case "lte": return [["eta_before", c.value]];
    case "gt":
    case "lt":
    case "eq":
    case "ne":
    case "in":
    case "nin":
    case "like":
      return [["attr", `eta:${c.op}:${c.value}`]];
    default:
      throw new DSLError(`unsupported operator on eta: ${c.op} (clause ${raw})`);
  }
}

/** Render a clause back into canonical DSL text — used to draw chips. */
export function renderClause(c: Clause): string {
  const symbolFor: Partial<Record<OpCode, string>> = {
    eq: "=", ne: "!=", gte: ">=", lte: "<=", gt: ">", lt: "<",
  };
  const sym = symbolFor[c.op];
  if (sym) return `${c.lhs}${sym}${c.value}`;
  if (c.op === "in")      return `${c.lhs} in ${c.value}`;
  if (c.op === "nin")     return `${c.lhs} not in ${c.value}`;
  if (c.op === "like")    return `${c.lhs} like ${c.value}`;
  if (c.op === "exists")  return `${c.lhs} exists`;
  if (c.op === "nexists") return `${c.lhs} nexists`;
  return `${c.lhs} ${c.op} ${c.value}`;
}
