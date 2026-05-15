import { describe, it, expect } from "vitest";
import {
  buildMailto,
  buildPlainBody,
  countOpen,
  defaultSubject,
  isoWeek,
  looksLikeEmail,
  parseCcList,
  partitionOwners,
  truncateBodyForMailto,
} from "../src/components/Kanban/emailFormat";
import type { Task } from "../src/api/client";

const mkTask = (over: Partial<Task> = {}): Task => ({
  id: 1,
  task_uuid: "T-ABC123",
  slug: "fix-login",
  title: "Fix login redirect",
  status: "todo",
  kind: "task",
  owners: ["nsaddaga@intel.com"],
  projects: ["VegaNotes"],
  features: [],
  attrs: {},
  eta: "ww19",
  priority_rank: 1,
  parent_task_id: null,
  note_id: 5,
  ...over,
});

describe("looksLikeEmail", () => {
  it("accepts standard addresses", () => {
    expect(looksLikeEmail("foo@bar.com")).toBe(true);
    expect(looksLikeEmail("a.b+c@sub.intel.com")).toBe(true);
  });
  it("rejects bare tokens", () => {
    expect(looksLikeEmail("nsaddaga")).toBe(false);
    expect(looksLikeEmail("@nsaddaga")).toBe(false);
    expect(looksLikeEmail("Naveen Saddagangadhar")).toBe(false);
    expect(looksLikeEmail("")).toBe(false);
  });
});

describe("partitionOwners", () => {
  it("splits emails from bare tokens, dedupes, lowercases", () => {
    const r = partitionOwners(["Foo@bar.com", "foo@bar.com", "alice", "alice", "bob@x.io"]);
    expect(r.resolved).toEqual(["bob@x.io", "foo@bar.com"]);
    expect(r.unresolved).toEqual(["alice"]);
  });
  it("ignores empty/whitespace tokens", () => {
    expect(partitionOwners(["", "  ", "x@y.com"]).resolved).toEqual(["x@y.com"]);
  });
});

describe("parseCcList", () => {
  it("splits on comma, semicolon, whitespace and dedupes", () => {
    expect(parseCcList("a@x.com, b@x.com;a@x.com  c@x.com")).toEqual([
      "a@x.com",
      "b@x.com",
      "c@x.com",
    ]);
  });
  it("returns empty for empty input", () => {
    expect(parseCcList("")).toEqual([]);
    expect(parseCcList("   ")).toEqual([]);
  });
});

describe("isoWeek", () => {
  it("matches ISO 8601 reference dates", () => {
    expect(isoWeek(new Date(Date.UTC(2026, 0, 1)))).toBe(1);
    expect(isoWeek(new Date(Date.UTC(2026, 4, 14)))).toBe(20);
    // Year boundary: 2025-12-29 (Mon) is week 1 of 2026 in ISO terms
    expect(isoWeek(new Date(Date.UTC(2025, 11, 29)))).toBe(1);
  });
});

describe("defaultSubject", () => {
  it("includes project, counts, padded ww", () => {
    const s = defaultSubject({ project: "VegaNotes", openCount: 7, blockedCount: 2, week: 5 });
    expect(s).toBe("[VegaNotes] VegaNotes kanban — 7 open, 2 blocked — ww05");
  });
  it("falls back to 'All projects'", () => {
    const s = defaultSubject({ openCount: 0, blockedCount: 0, week: 1 });
    expect(s).toContain("All projects");
  });
});

describe("countOpen", () => {
  it("sums todo + in-progress + blocked, excludes done", () => {
    expect(countOpen({
      todo: [mkTask(), mkTask()],
      "in-progress": [mkTask()],
      blocked: [mkTask()],
      done: [mkTask(), mkTask(), mkTask()],
    })).toBe(4);
  });
});

describe("buildPlainBody", () => {
  const grouped = {
    todo: [mkTask({ title: "Fix login", owners: ["nsaddaga@intel.com"], task_uuid: "T-A" })],
    "in-progress": [mkTask({ title: "Refactor", priority_rank: 2, eta: null, owners: ["bob"], task_uuid: "T-B" })],
    blocked: [],
    done: [mkTask({ title: "Old thing", status: "done", task_uuid: "T-C" })],
  };
  const cols = ["todo", "in-progress", "blocked", "done"] as const;

  it("includes headers, filters, and skips Done by default", () => {
    const body = buildPlainBody({
      filters: { project: "VegaNotes", hide_done: true, where: ["@area=fit"] },
      grouped,
      columns: cols,
      snapshotUrl: "http://example.com/k",
      includeDone: false,
      generatedAt: new Date(Date.UTC(2026, 4, 14, 12, 0)),
    });
    expect(body).toContain("VegaNotes — Kanban snapshot");
    expect(body).toContain("Generated: 2026-05-14 12:00 UTC");
    expect(body).toContain("project=VegaNotes");
    expect(body).toContain("chips=[@area=fit]");
    expect(body).toContain("View: http://example.com/k");
    expect(body).toContain("== TODO (1) ==");
    expect(body).toContain("[P1] Fix login");
    expect(body).toContain("@nsaddaga@intel.com");
    expect(body).toContain("== IN-PROGRESS (1) ==");
    expect(body).toContain("[P2] Refactor");
    expect(body).not.toContain("== DONE");
    expect(body).not.toContain("Old thing");
  });

  it("includes Done when toggled", () => {
    const body = buildPlainBody({
      filters: {},
      grouped,
      columns: cols,
      snapshotUrl: "x",
      includeDone: true,
    });
    expect(body).toContain("== DONE (1) ==");
    expect(body).toContain("Old thing");
  });

  it("omits empty columns", () => {
    const body = buildPlainBody({
      filters: {},
      grouped: { todo: [], "in-progress": [], blocked: [], done: [] },
      columns: cols,
      snapshotUrl: "x",
      includeDone: true,
    });
    expect(body).not.toContain("== TODO");
    expect(body).not.toContain("== DONE");
  });

  it("renders '(no title)' for blank-titled tasks", () => {
    const body = buildPlainBody({
      filters: {},
      grouped: { todo: [mkTask({ title: "", owners: [], eta: null, task_uuid: null })], "in-progress": [], blocked: [], done: [] },
      columns: cols,
      snapshotUrl: "x",
      includeDone: false,
    });
    expect(body).toContain("(no title)");
  });
});

describe("buildMailto", () => {
  it("builds a valid mailto URL with To, CC, subject, body", () => {
    const r = buildMailto({
      to: ["a@x.com", "b@x.com"],
      cc: ["c@x.com"],
      subject: "hi & bye",
      body: "line1\nline2",
    });
    expect(r.url).toMatch(/^mailto:a%40x\.com,b%40x\.com\?/);
    expect(r.url).toContain("cc=c%40x.com");
    expect(r.url).toContain("subject=hi%20%26%20bye");
    expect(r.url).toContain("body=line1%0Aline2");
    expect(r.tooLong).toBe(false);
  });

  it("flags too-long URLs", () => {
    const big = "x".repeat(2000);
    const r = buildMailto({ to: ["a@x.com"], cc: [], subject: "s", body: big });
    expect(r.tooLong).toBe(true);
    expect(r.length).toBeGreaterThan(1800);
  });

  it("works with no To recipients (CC-only sends)", () => {
    const r = buildMailto({ to: [], cc: ["c@x.com"], subject: "s", body: "b" });
    expect(r.url.startsWith("mailto:?")).toBe(true);
  });
});

describe("truncateBodyForMailto", () => {
  it("returns body unchanged when short", () => {
    expect(truncateBodyForMailto("hello")).toBe("hello");
  });
  it("trims and adds footer when too long", () => {
    const out = truncateBodyForMailto("a".repeat(5000));
    expect(out.length).toBeLessThanOrEqual(1400);
    expect(out).toContain("truncated");
  });
});
