// VegaNotes parser (TypeScript). Mirrors backend/app/parser/parser.py.
// Returns the same JSON shape; verified by shared golden fixtures.

import { REGISTRY, isKnown } from "./tokens.ts";

const SLUG_RE = /[^a-z0-9]+/g;
export function slugify(text: string): string {
  return text.trim().toLowerCase().replace(SLUG_RE, "-").replace(/^-|-$/g, "") || "task";
}

interface Token {
  kind: "task_decl" | "attr";
  name: string;
  value: string;
  raw: string;
  col: number;
}

interface TextChunk {
  kind: "text";
  text: string;
}

type Item = Token | TextChunk;

const TOKEN_RE = /(!task|!AR)|#([a-zA-Z][\w-]*)|(?:^|(?<=[\s([]))@([a-zA-Z][\w.-]*)/g;

function readValue(s: string, i: number, untilHash = false): [string, number] {
  const n = s.length;
  while (i < n && (s[i] === " " || s[i] === "\t")) i++;
  if (i < n && s[i] === '"') {
    let j = i + 1;
    const out: string[] = [];
    while (j < n && s[j] !== '"') {
      if (s[j] === "\\" && j + 1 < n) { out.push(s[j + 1]); j += 2; } else { out.push(s[j]); j++; }
    }
    return [out.join(""), j < n ? j + 1 : j];
  }
  if (untilHash) {
    let j = i;
    while (j < n) {
      if (s[j] === "#") {
        const m = /^#([a-zA-Z][\w-]*)/.exec(s.slice(j));
        if (m && isKnown(m[1])) break;
      }
      if (s[j] === "@" && (j === 0 || s[j - 1] === " " || s[j - 1] === "\t" || s[j - 1] === "(" || s[j - 1] === "[")
          && j + 1 < n && /[a-zA-Z_]/.test(s[j + 1])) {
        break;
      }
      j++;
    }
    return [s.slice(i, j).replace(/\s+$/, ""), j];
  }
  let j = i;
  while (j < n && s[j] !== " " && s[j] !== "\t") j++;
  return [s.slice(i, j), j];
}

function lex(line: string): Item[] {
  const out: Item[] = [];
  let last = 0;
  TOKEN_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TOKEN_RE.exec(line))) {
    const start = m.index;
    if (start > last) out.push({ kind: "text", text: line.slice(last, start) });
    if (m[1]) {
      const bang = m[1];
      const kindName = bang === "!AR" ? "ar" : "task";
      const [value, end] = readValue(line, TOKEN_RE.lastIndex, true);
      out.push({ kind: "task_decl", name: kindName, value, raw: line.slice(start, end), col: start });
      TOKEN_RE.lastIndex = end;
      last = end;
    } else if (m[3] !== undefined) {
      const user = m[3];
      const end = TOKEN_RE.lastIndex;
      out.push({ kind: "attr", name: "owner", value: user, raw: line.slice(start, end), col: start });
      last = end;
    } else {
      const name = m[2];
      if (!isKnown(name)) {
        const end = TOKEN_RE.lastIndex;
        out.push({ kind: "text", text: line.slice(start, end) });
        last = end;
        continue;
      }
      const [value, end] = readValue(line, TOKEN_RE.lastIndex);
      out.push({ kind: "attr", name, value, raw: line.slice(start, end), col: start });
      TOKEN_RE.lastIndex = end;
      last = end;
    }
  }
  if (last < line.length) out.push({ kind: "text", text: line.slice(last) });
  return out;
}

function indentLevel(line: string): number {
  const m = /^(\s*)/.exec(line);
  if (!m) return 0;
  let width = 0;
  for (const ch of m[1]) width += ch === "\t" ? 4 : 1;
  return Math.floor(width / 2);
}

export interface ParsedTask {
  slug: string;
  title: string;
  line: number;
  indent: number;
  kind: string;
  parent_slug: string | null;
  status: string;
  attrs: Record<string, string | string[]>;
  attrs_norm: Record<string, unknown>;
  refs: { kind: string; dst_slug: string }[];
}

export interface ParseResult {
  tasks: ParsedTask[];
  refs: { src_slug: string; dst_slug: string; kind: string }[];
}

const BULLET_STRIP = /^\s*([-*+]\s+|\d+[.)]\s+)?/;

function isContextOnlyLine(items: Item[]): boolean {
  for (const x of items) {
    if (x.kind === "text") {
      const t = x.text.replace(BULLET_STRIP, "").trim();
      if (t) return false;
    }
  }
  return true;
}

function attachAttr(task: ParsedTask, tok: Token): void {
  const spec = REGISTRY[tok.name];
  if (tok.name === "status") task.status = tok.value.trim().toLowerCase() || "todo";
  if (tok.name === "task" || tok.name === "link") {
    task.refs.push({ kind: tok.name, dst_slug: slugify(tok.value) });
    return;
  }
  if (spec.multi) {
    const cur = task.attrs[tok.name];
    if (Array.isArray(cur)) {
      if (!cur.includes(tok.value)) cur.push(tok.value);
    } else {
      task.attrs[tok.name] = [tok.value];
    }
  } else {
    task.attrs[tok.name] = tok.value;
  }
  if (spec.normalize) {
    const norm = spec.normalize(tok.value);
    if (spec.multi) {
      const cur = task.attrs_norm[tok.name];
      if (Array.isArray(cur)) {
        if (!(cur as unknown[]).includes(norm)) (cur as unknown[]).push(norm);
      } else {
        task.attrs_norm[tok.name] = [norm];
      }
    } else if (norm !== null && norm !== undefined) {
      task.attrs_norm[tok.name] = norm;
    }
  }
}

function mergeInherited(task: ParsedTask, inherited: Record<string, string[]>): void {
  for (const [key, values] of Object.entries(inherited)) {
    const spec = REGISTRY[key];
    if (!spec) continue;
    if (spec.multi) {
      const existing = task.attrs[key];
      const arr: string[] = Array.isArray(existing) ? [...existing] : (existing ? [existing] : []);
      for (const v of values) if (!arr.includes(v)) arr.push(v);
      if (arr.length) {
        task.attrs[key] = arr;
        if (spec.normalize) task.attrs_norm[key] = arr.map((v) => spec.normalize!(v));
      }
    } else {
      if (!(key in task.attrs) && values.length) {
        task.attrs[key] = values[0];
        if (spec.normalize) task.attrs_norm[key] = spec.normalize(values[0]);
      }
    }
  }
}

function inheritFromParent(task: ParsedTask, parent: ParsedTask): void {
  for (const [k, v] of Object.entries(parent.attrs)) {
    const spec = REGISTRY[k];
    if (!spec || !spec.multi) continue;
    const existing = task.attrs[k];
    const arr: string[] = Array.isArray(existing) ? [...existing] : (existing ? [existing] : []);
    const items = Array.isArray(v) ? v : [v];
    for (const item of items) if (!arr.includes(item)) arr.push(item);
    task.attrs[k] = arr;
    if (spec.normalize) task.attrs_norm[k] = arr.map((x) => spec.normalize!(x));
  }
}

export function parse(md: string): ParseResult {
  const tasks: ParsedTask[] = [];
  const stack: ParsedTask[] = [];
  let current: ParsedTask | null = null;
  const taken: Record<string, number> = {};
  const ctxStack: Record<string, string[]>[] = [];

  const slugCollide = (slug: string): string => {
    if (!(slug in taken)) { taken[slug] = 1; return slug; }
    taken[slug] += 1; return `${slug}-${taken[slug]}`;
  };

  const gatherInherited = (): Record<string, string[]> => {
    const merged: Record<string, string[]> = {};
    for (const attrs of ctxStack) {
      for (const [k, vs] of Object.entries(attrs)) {
        const bucket = merged[k] || (merged[k] = []);
        for (const v of vs) if (!bucket.includes(v)) bucket.push(v);
      }
    }
    return merged;
  };

  const lines = md.split("\n");
  for (let lineNo = 0; lineNo < lines.length; lineNo++) {
    const raw = lines[lineNo];
    if (!raw.trim()) { current = null; stack.length = 0; ctxStack.length = 0; continue; }
    const items = lex(raw);
    const decl = items.find((x) => x.kind === "task_decl") as Token | undefined;
    const attrs = items.filter((x) => x.kind === "attr") as Token[];

    if (decl) {
      const indent = indentLevel(raw);
      const kind = decl.name === "ar" ? "ar" : "task";
      const task: ParsedTask = {
        slug: slugCollide(slugify(decl.value)),
        title: decl.value.trim(),
        line: lineNo,
        indent,
        kind,
        parent_slug: null,
        status: "todo",
        attrs: {},
        attrs_norm: {},
        refs: [],
      };
      while (stack.length && stack[stack.length - 1].indent >= indent) stack.pop();
      if (stack.length) task.parent_slug = stack[stack.length - 1].slug;
      for (const t of attrs) attachAttr(task, t);
      mergeInherited(task, gatherInherited());
      if (stack.length) inheritFromParent(task, stack[stack.length - 1]);
      stack.push(task); tasks.push(task); current = task;
    } else if (attrs.length) {
      if (isContextOnlyLine(items)) {
        const ctxAttrs: Record<string, string[]> = {};
        for (const t of attrs) (ctxAttrs[t.name] || (ctxAttrs[t.name] = [])).push(t.value);
        ctxStack.push(ctxAttrs);
      } else if (current) {
        for (const t of attrs) attachAttr(current, t);
      }
    }
  }

  const refs = tasks.flatMap((t) =>
    t.refs.map((r) => ({ src_slug: t.slug, dst_slug: r.dst_slug, kind: r.kind })),
  );
  return { tasks, refs };
}
