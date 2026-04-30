import { describe, it, expect, beforeEach } from "vitest";
import {
  useSidebarPrefs,
  applySidebarPrefs,
  projectKey,
  projectLabel,
  NO_PROJECT_KEY,
} from "../src/store/sidebarPrefs.ts";
import type { TreeNode } from "../src/api/client";

function mkNode(
  project: string | null,
  notes: { path: string; title: string }[] = [],
  role: "manager" | "member" = "member",
): TreeNode {
  return {
    project,
    role,
    notes: notes.map((n) => ({ id: 1, path: n.path, title: n.title })),
  };
}

beforeEach(() => {
  useSidebarPrefs.setState({ collapsed: {}, hidden: {} });
  // Wipe persisted localStorage so each test starts clean.
  try {
    localStorage.removeItem("vega:sidebar:v1");
  } catch {
    /* jsdom may or may not expose localStorage depending on env */
  }
});

describe("projectKey / projectLabel", () => {
  it("uses NO_PROJECT_KEY for null project nodes", () => {
    const n = mkNode(null);
    expect(projectKey(n)).toBe(NO_PROJECT_KEY);
    expect(projectLabel(n)).toBe("(no project)");
  });

  it("returns the project string when present", () => {
    const n = mkNode("alpha");
    expect(projectKey(n)).toBe("alpha");
    expect(projectLabel(n)).toBe("alpha");
  });
});

describe("useSidebarPrefs store", () => {
  it("toggleCollapse adds and removes a key", () => {
    const { toggleCollapse } = useSidebarPrefs.getState();
    toggleCollapse("alpha");
    expect(useSidebarPrefs.getState().collapsed).toEqual({ alpha: true });
    toggleCollapse("alpha");
    expect(useSidebarPrefs.getState().collapsed).toEqual({});
  });

  it("collapseAll replaces collapsed set with the supplied keys", () => {
    useSidebarPrefs.setState({ collapsed: { stale: true } });
    useSidebarPrefs.getState().collapseAll(["a", "b", "c"]);
    expect(useSidebarPrefs.getState().collapsed).toEqual({
      a: true,
      b: true,
      c: true,
    });
  });

  it("expandAll clears every collapsed key", () => {
    useSidebarPrefs.setState({ collapsed: { a: true, b: true } });
    useSidebarPrefs.getState().expandAll();
    expect(useSidebarPrefs.getState().collapsed).toEqual({});
  });

  it("hideProject and unhideProject toggle the hidden map", () => {
    const s = useSidebarPrefs.getState();
    s.hideProject("alpha");
    s.hideProject("beta");
    expect(useSidebarPrefs.getState().hidden).toEqual({
      alpha: true,
      beta: true,
    });
    s.unhideProject("alpha");
    expect(useSidebarPrefs.getState().hidden).toEqual({ beta: true });
  });

  it("reset clears both collapsed and hidden maps", () => {
    useSidebarPrefs.setState({
      collapsed: { a: true },
      hidden: { b: true },
    });
    useSidebarPrefs.getState().reset();
    expect(useSidebarPrefs.getState()).toMatchObject({
      collapsed: {},
      hidden: {},
    });
  });
});

describe("applySidebarPrefs (no search)", () => {
  const tree: TreeNode[] = [
    mkNode("alpha", [
      { path: "alpha/a.md", title: "Alpha One" },
      { path: "alpha/b.md", title: "Alpha Two" },
    ]),
    mkNode("beta", [{ path: "beta/x.md", title: "Beta X" }]),
    mkNode(null, [{ path: "loose.md", title: "Loose" }]),
  ];

  it("renders every project expanded by default", () => {
    const out = applySidebarPrefs(tree, { collapsed: {}, hidden: {} }, "");
    expect(out.searchActive).toBe(false);
    expect(out.hiddenProjects).toEqual([]);
    expect(out.visibleProjects).toHaveLength(3);
    expect(out.visibleProjects.every((p) => p.expanded)).toBe(true);
    expect(out.visibleProjects[0].notes).toHaveLength(2);
  });

  it("collapsed projects still render but with expanded=false", () => {
    const out = applySidebarPrefs(
      tree,
      { collapsed: { alpha: true }, hidden: {} },
      "",
    );
    const alpha = out.visibleProjects.find((p) => p.node.project === "alpha")!;
    const beta = out.visibleProjects.find((p) => p.node.project === "beta")!;
    expect(alpha.expanded).toBe(false);
    expect(beta.expanded).toBe(true);
  });

  it("hidden projects vanish from the visible list and surface in hiddenProjects", () => {
    const out = applySidebarPrefs(
      tree,
      { collapsed: {}, hidden: { beta: true } },
      "",
    );
    expect(out.visibleProjects.map((p) => p.node.project)).toEqual([
      "alpha",
      null,
    ]);
    expect(out.hiddenProjects.map((p) => p.project)).toEqual(["beta"]);
  });

  it("can hide the (no project) group via NO_PROJECT_KEY", () => {
    const out = applySidebarPrefs(
      tree,
      { collapsed: {}, hidden: { [NO_PROJECT_KEY]: true } },
      "",
    );
    expect(out.visibleProjects.map((p) => p.node.project)).toEqual([
      "alpha",
      "beta",
    ]);
    expect(out.hiddenProjects).toHaveLength(1);
    expect(out.hiddenProjects[0].project).toBeNull();
  });
});

describe("applySidebarPrefs (search override)", () => {
  const tree: TreeNode[] = [
    mkNode("alpha", [
      { path: "alpha/intro.md", title: "Intro" },
      { path: "alpha/design.md", title: "Design Doc" },
    ]),
    mkNode("beta", [{ path: "beta/notes.md", title: "Daily Notes" }]),
    mkNode("gamma-design", [{ path: "gamma-design/x.md", title: "X" }]),
  ];

  it("returns only projects with matching name or notes", () => {
    const out = applySidebarPrefs(
      tree,
      { collapsed: {}, hidden: {} },
      "design",
    );
    expect(out.searchActive).toBe(true);
    const names = out.visibleProjects.map((p) => p.node.project);
    expect(names).toContain("alpha");          // note title matches
    expect(names).toContain("gamma-design");   // project name matches
    expect(names).not.toContain("beta");
  });

  it("filters notes within a project to only matching ones", () => {
    const out = applySidebarPrefs(
      tree,
      { collapsed: {}, hidden: {} },
      "design",
    );
    const alpha = out.visibleProjects.find((p) => p.node.project === "alpha")!;
    expect(alpha.notes.map((n) => n.title)).toEqual(["Design Doc"]);
  });

  it("when project name matches, all its notes are kept", () => {
    const out = applySidebarPrefs(
      tree,
      { collapsed: {}, hidden: {} },
      "gamma",
    );
    const g = out.visibleProjects.find((p) => p.node.project === "gamma-design")!;
    expect(g.notes).toHaveLength(1);
  });

  it("forces every visible project to expanded, ignoring collapse state", () => {
    const out = applySidebarPrefs(
      tree,
      { collapsed: { alpha: true }, hidden: {} },
      "design",
    );
    const alpha = out.visibleProjects.find((p) => p.node.project === "alpha")!;
    expect(alpha.expanded).toBe(true);
  });

  it("surfaces hidden projects when search matches them, marked hiddenByPref", () => {
    const out = applySidebarPrefs(
      tree,
      { collapsed: {}, hidden: { "gamma-design": true } },
      "gamma",
    );
    const g = out.visibleProjects.find((p) => p.node.project === "gamma-design");
    expect(g).toBeDefined();
    expect(g!.hiddenByPref).toBe(true);
    // Hidden-but-matched projects do NOT also appear in hiddenProjects (the
    // popover) — they're already on screen.
    expect(out.hiddenProjects).toEqual([]);
  });

  it("hidden projects with no search match remain in hiddenProjects only", () => {
    const out = applySidebarPrefs(
      tree,
      { collapsed: {}, hidden: { beta: true } },
      "design",
    );
    expect(out.visibleProjects.find((p) => p.node.project === "beta")).toBeUndefined();
    expect(out.hiddenProjects.map((p) => p.project)).toEqual(["beta"]);
  });

  it("matching is case-insensitive and substring-based", () => {
    const out = applySidebarPrefs(
      tree,
      { collapsed: {}, hidden: {} },
      "DESI",
    );
    const names = out.visibleProjects.map((p) => p.node.project);
    expect(names).toContain("alpha");
    expect(names).toContain("gamma-design");
  });

  it("whitespace-only search is treated as no search", () => {
    const out = applySidebarPrefs(
      tree,
      { collapsed: {}, hidden: { beta: true } },
      "   ",
    );
    expect(out.searchActive).toBe(false);
    expect(out.hiddenProjects).toHaveLength(1);
  });
});

describe("applySidebarPrefs persistence interplay", () => {
  it("snapshots from the live store can be passed straight in", () => {
    useSidebarPrefs.getState().toggleCollapse("alpha");
    useSidebarPrefs.getState().hideProject("beta");
    const snap = useSidebarPrefs.getState();
    const tree: TreeNode[] = [
      mkNode("alpha", [{ path: "alpha/a.md", title: "A" }]),
      mkNode("beta", [{ path: "beta/b.md", title: "B" }]),
    ];
    const out = applySidebarPrefs(
      tree,
      { collapsed: snap.collapsed, hidden: snap.hidden },
      "",
    );
    expect(out.visibleProjects).toHaveLength(1);
    expect(out.visibleProjects[0].node.project).toBe("alpha");
    expect(out.visibleProjects[0].expanded).toBe(false);
    expect(out.hiddenProjects[0].project).toBe("beta");
  });
});
