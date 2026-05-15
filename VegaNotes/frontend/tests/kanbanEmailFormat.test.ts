import { describe, it, expect } from "vitest";
import {
  buildMailto,
  buildOwnerStatusRows,
  buildPlainBody,
  countOpen,
  defaultSubject,
  isoWeek,
  looksLikeEmail,
  parseCcList,
  partitionOwners,
  renderOwnerStatusTable,
  truncateBodyForMailto,
} from "../src/components/Kanban/emailFormat";
import type { PhonebookEntry, Task } from "../src/api/client";

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
  it("phonebook resolves bare tokens to email", () => {
    const pb: Record<string, PhonebookEntry> = {
      "@nsaddaga": {
        idsid: "nsaddaga", display: "Prasad Addagarla",
        email: "prasad.addagarla@intel.com", aliases: [], manager_email: null,
      },
    };
    const r = partitionOwners(["@nsaddaga", "alice"], pb);
    expect(r.resolved).toEqual(["prasad.addagarla@intel.com"]);
    expect(r.unresolved).toEqual(["alice"]);
    expect(r.displayByEmail["prasad.addagarla@intel.com"]).toBe("Prasad Addagarla");
  });
  it("phonebook lookup is case-insensitive on the bare token", () => {
    const pb: Record<string, PhonebookEntry> = {
      nsaddaga: {
        idsid: "nsaddaga", display: "Prasad", email: "p@x.com",
        aliases: [], manager_email: null,
      },
    };
    const r = partitionOwners(["NSADDAGA"], pb);
    expect(r.resolved).toEqual(["p@x.com"]);
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

describe("buildOwnerStatusRows + renderOwnerStatusTable", () => {
  const tasks: Task[] = [
    mkTask({ owners: ["alice"], status: "todo" }),
    mkTask({ owners: ["alice"], status: "in-progress" }),
    mkTask({ owners: ["alice", "bob"], status: "done" }),
    mkTask({ owners: ["bob"], status: "blocked" }),
    mkTask({ owners: [], status: "todo" }),
  ];

  it("aggregates per owner including unassigned", () => {
    const rows = buildOwnerStatusRows(tasks);
    const byOwner = Object.fromEntries(rows.map((r) => [r.owner, r]));
    expect(byOwner["@alice"].todo).toBe(1);
    expect(byOwner["@alice"].inProgress).toBe(1);
    expect(byOwner["@alice"].done).toBe(1);
    expect(byOwner["@alice"].total).toBe(3);
    expect(byOwner["@bob"].blocked).toBe(1);
    expect(byOwner["@bob"].done).toBe(1);
    expect(byOwner["@bob"].total).toBe(2);
    expect(byOwner["(unassigned)"].todo).toBe(1);
  });

  it("uses phonebook display name + canonical email key", () => {
    const pb: Record<string, PhonebookEntry> = {
      alice: { idsid: "alice", display: "Alice Smith", email: "alice@x.com", aliases: [], manager_email: null },
    };
    const rows = buildOwnerStatusRows(tasks, pb);
    const alice = rows.find((r) => r.owner === "Alice Smith");
    expect(alice).toBeDefined();
    expect(alice!.email).toBe("alice@x.com");
    expect(alice!.total).toBe(3);
  });

  it("renders ASCII table with header, separator, totals", () => {
    const rows = buildOwnerStatusRows(tasks);
    const table = renderOwnerStatusTable(rows);
    expect(table).toContain("Owner");
    expect(table).toContain("Todo");
    expect(table).toContain("WIP");
    expect(table).toContain("Total");
    // Last row totals all tasks (alice 3 + bob 2 + unassigned 1 = 6)
    expect(table.split("\n").pop()).toContain("6");
    // Sorted by total desc → alice first.
    expect(table).toMatch(/@alice[^\n]*3/);
  });

  it("returns '(no tasks)' for empty input", () => {
    expect(renderOwnerStatusTable([])).toBe("(no tasks)");
  });
});

describe("buildPlainBody with phonebook", () => {
  it("includes status-by-owner table and uses canonical IDSID in card lines", () => {
    const pb: Record<string, PhonebookEntry> = {
      "@addagarla": {
        idsid: "nsaddaga", display: "Prasad Addagarla",
        email: "prasad@intel.com", aliases: ["addagarla"], manager_email: null,
      },
    };
    const grouped = {
      todo: [mkTask({ title: "X", owners: ["@addagarla"], task_uuid: "T-X" })],
      "in-progress": [], blocked: [], done: [],
    };
    const body = buildPlainBody({
      filters: {},
      grouped,
      columns: ["todo", "in-progress", "blocked", "done"] as const,
      snapshotUrl: "url",
      includeDone: false,
      phonebook: pb,
    });
    expect(body).toContain("== STATUS BY OWNER ==");
    expect(body).toContain("Prasad Addagarla");
    // Card line should use canonical idsid, not the original token.
    expect(body).toContain("@nsaddaga");
    expect(body).not.toContain("@@addagarla");
  });
});

// ---------------------------------------------------------------------------
// HTML body (#219).
// ---------------------------------------------------------------------------
import { buildHtmlBody, computeArStats } from "../src/components/Kanban/emailFormat";

const baseFilters = { project: "Demo", owner: "", feature: "", priority: "", status: "", q: "", where: [] } as any;

describe("buildHtmlBody", () => {
  const cols = ["blocked", "in-progress", "todo", "done"] as const;

  it("renders a project header with snapshot URL and filter summary", () => {
    const html = buildHtmlBody({
      filters: baseFilters, grouped: { todo: [mkTask()] }, columns: cols,
      snapshotUrl: "http://example/snap", includeDone: false,
    });
    expect(html).toContain("Demo");
    expect(html).toContain("Kanban snapshot");
    expect(html).toContain("http://example/snap");
    expect(html).toContain("(no filters)");
  });

  it("renders columns in order with status-colored headers", () => {
    const html = buildHtmlBody({
      filters: baseFilters,
      grouped: {
        blocked: [mkTask({ id: 1, title: "Blk task", status: "blocked" })],
        "in-progress": [mkTask({ id: 2, title: "WIP task", status: "in-progress" })],
        todo: [mkTask({ id: 3, title: "Todo task", status: "todo" })],
      },
      columns: cols, snapshotUrl: "", includeDone: false,
    });
    // Blocked appears before in-progress before todo in the output order.
    const iBlocked = html.indexOf("BLOCKED");
    const iWIP = html.indexOf("IN-PROGRESS");
    const iTodo = html.indexOf("TODO");
    expect(iBlocked).toBeGreaterThan(0);
    expect(iWIP).toBeGreaterThan(iBlocked);
    expect(iTodo).toBeGreaterThan(iWIP);
    // Color tokens for each column header are present.
    expect(html).toContain("#dc2626"); // blocked red
    expect(html).toContain("#2563eb"); // in-progress blue
    expect(html).toContain("#475569"); // todo slate
  });

  it("escapes HTML in task titles and owner tokens (XSS guard)", () => {
    const html = buildHtmlBody({
      filters: baseFilters,
      grouped: { todo: [mkTask({ title: "<script>x</script>", owners: ["<img onerror>"] })] },
      columns: cols, snapshotUrl: "", includeDone: false,
    });
    expect(html).not.toContain("<script>x</script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("&lt;img onerror&gt;");
  });

  it("excludes the Done column when includeDone=false, includes when true", () => {
    const grouped = { done: [mkTask({ status: "done", title: "Closed item" })] };
    const off = buildHtmlBody({ filters: baseFilters, grouped, columns: cols, snapshotUrl: "", includeDone: false });
    const on  = buildHtmlBody({ filters: baseFilters, grouped, columns: cols, snapshotUrl: "", includeDone: true });
    expect(off).not.toContain("Closed item");
    expect(on).toContain("Closed item");
    expect(on).toContain("DONE");
  });

  it("renders the per-owner status table when there are visible tasks", () => {
    const html = buildHtmlBody({
      filters: baseFilters,
      grouped: { todo: [mkTask({ owners: ["@alice"] }), mkTask({ owners: ["@alice"], status: "todo" })] },
      columns: cols, snapshotUrl: "", includeDone: false,
    });
    expect(html).toContain("STATUS BY OWNER");
    expect(html).toContain("@alice");
  });

  it("emits no STATUS BY OWNER section when there are zero visible tasks", () => {
    const html = buildHtmlBody({
      filters: baseFilters, grouped: {}, columns: cols, snapshotUrl: "", includeDone: false,
    });
    expect(html).not.toContain("STATUS BY OWNER");
  });

  it("renders an AR stats chip bar with totals for each status", () => {
    const html = buildHtmlBody({
      filters: baseFilters,
      grouped: {
        blocked: [mkTask({ id: 1, status: "blocked" })],
        "in-progress": [mkTask({ id: 2, status: "in-progress" }), mkTask({ id: 3, status: "in-progress" })],
        todo: [mkTask({ id: 4, status: "todo" })],
        done: [mkTask({ id: 5, status: "done" }), mkTask({ id: 6, status: "done" })],
      },
      columns: [...cols], snapshotUrl: "", includeDone: true,
    });
    expect(html).toContain("Total: 6");
    expect(html).toContain("To-do: 1");
    expect(html).toContain("In-progress: 2");
    expect(html).toContain("Blocked: 1");
    expect(html).toContain("Done: 2");
    // open=4, done=2 → 33% complete
    expect(html).toContain("33% complete");
  });

  it("AR stats: total excludes done when includeDone=false; no completion %", () => {
    const html = buildHtmlBody({
      filters: baseFilters,
      grouped: {
        todo: [mkTask({ id: 1, status: "todo" })],
        done: [mkTask({ id: 2, status: "done" })],
      },
      columns: [...cols], snapshotUrl: "", includeDone: false,
    });
    expect(html).toContain("Total: 1");
    expect(html).toContain("Done: 1"); // still surfaced for context
    expect(html).not.toContain("% complete");
  });
});

describe("computeArStats", () => {
  const cols = ["blocked", "in-progress", "todo", "done"];
  it("counts each status and computes completion when done is included", () => {
    const s = computeArStats(
      {
        todo: [mkTask({ id: 1 }), mkTask({ id: 2 })],
        "in-progress": [mkTask({ id: 3 })],
        blocked: [mkTask({ id: 4 })],
        done: [mkTask({ id: 5 }), mkTask({ id: 6 }), mkTask({ id: 7 })],
      },
      cols,
      true,
    );
    expect(s).toEqual({
      total: 7, todo: 2, inProgress: 1, blocked: 1, done: 3, open: 4, completionPct: 43,
    });
  });
  it("excludes done from total and returns null completionPct when includeDone=false", () => {
    const s = computeArStats(
      { todo: [mkTask({ id: 1 })], done: [mkTask({ id: 2 })] },
      cols,
      false,
    );
    expect(s.total).toBe(1);
    expect(s.done).toBe(1);
    expect(s.completionPct).toBeNull();
  });
  it("returns zeros and null completion for empty input", () => {
    const s = computeArStats({}, cols, true);
    expect(s).toEqual({
      total: 0, todo: 0, inProgress: 0, blocked: 0, done: 0, open: 0, completionPct: null,
    });
  });
});

describe("buildPlainBody AR stats line", () => {
  const cols = ["blocked", "in-progress", "todo", "done"];
  it("includes an 'AR stats:' line with per-status counts and completion %", () => {
    const body = buildPlainBody({
      filters: baseFilters,
      grouped: {
        todo: [mkTask({ id: 1 })],
        "in-progress": [mkTask({ id: 2, status: "in-progress" })],
        done: [mkTask({ id: 3, status: "done" })],
      },
      columns: cols, snapshotUrl: "http://x", includeDone: true,
    });
    expect(body).toMatch(/AR stats: total=3, todo=1, in-progress=1, blocked=0, done=1, 33% complete/);
  });
});
