/**
 * #314: unit tests for external-link chip helpers.
 */
import { describe, it, expect } from "vitest";
import {
  buildHsdChip,
  buildJiraChip,
  buildPrChip,
  buildUrlChip,
  buildLinkChips,
  urlDisplayLabel,
  HSD_BASE,
  JIRA_BASE,
  GITHUB_BASE,
} from "../src/lib/linkChips";

describe("urlDisplayLabel", () => {
  it("returns hostname for well-formed https URLs", () => {
    expect(urlDisplayLabel("https://example.com/foo")).toBe("example.com");
    expect(urlDisplayLabel("https://sub.example.com/very/long/path")).toBe("sub.example.com");
  });
  it("truncates non-URL input past 40 chars", () => {
    const long = "x".repeat(60);
    const out = urlDisplayLabel(long);
    expect(out).toContain("…");
    expect(out.length).toBeLessThanOrEqual(41);
  });
  it("returns short raw for short non-URL input", () => {
    expect(urlDisplayLabel("hello")).toBe("hello");
  });
});

describe("buildHsdChip", () => {
  it("labels numeric IDs as HSD-N", () => {
    const c = buildHsdChip("1234567")!;
    expect(c.label).toBe("HSD-1234567");
    expect(c.href).toBe(`${HSD_BASE}1234567`);
    expect(c.kind).toBe("hsd");
  });
  it("passes through non-numeric IDs verbatim as label", () => {
    const c = buildHsdChip("VEG-99")!;
    expect(c.label).toBe("VEG-99");
  });
  it("returns null for empty input", () => {
    expect(buildHsdChip("")).toBeNull();
    expect(buildHsdChip("   ")).toBeNull();
  });
});

describe("buildJiraChip", () => {
  it("wraps key in devtools jira browse URL", () => {
    const c = buildJiraChip("ABC-42")!;
    expect(c.label).toBe("ABC-42");
    expect(c.href).toBe(`${JIRA_BASE}ABC-42`);
    expect(c.kind).toBe("jira");
  });
  it("returns null for empty", () => {
    expect(buildJiraChip("")).toBeNull();
  });
});

describe("buildPrChip", () => {
  it("parses owner/repo#N into a GitHub PR URL", () => {
    const c = buildPrChip("intelprasada/veganotes#309")!;
    expect(c.label).toBe("intelprasada/veganotes#309");
    expect(c.href).toBe(`${GITHUB_BASE}intelprasada/veganotes/pull/309`);
    expect(c.kind).toBe("pr");
  });
  it("falls back to repo home for bare owner/repo", () => {
    const c = buildPrChip("owner/repo")!;
    expect(c.href).toBe(`${GITHUB_BASE}owner/repo`);
    expect(c.label).toBe("owner/repo");
  });
  it("returns null for invalid PR specs", () => {
    expect(buildPrChip("")).toBeNull();
    expect(buildPrChip("notaslash")).toBeNull();
  });
});

describe("buildUrlChip", () => {
  it("returns hostname label + href for a plain URL", () => {
    const c = buildUrlChip("https://example.com/foo")!;
    expect(c.label).toBe("example.com");
    expect(c.href).toBe("https://example.com/foo");
    expect(c.kind).toBe("url");
  });
  it("supports LABEL:url shorthand", () => {
    const c = buildUrlChip("Design:https://example.com/design")!;
    expect(c.label).toBe("Design");
    expect(c.href).toBe("https://example.com/design");
  });
  it("rejects non-http(s) values", () => {
    expect(buildUrlChip("ftp://example.com")).toBeNull();
    expect(buildUrlChip("just text")).toBeNull();
    expect(buildUrlChip("")).toBeNull();
  });
  it("treats a bogus LABEL:not-a-url as a raw fallthrough (still rejected)", () => {
    expect(buildUrlChip("Foo:bar")).toBeNull();
  });
});

describe("buildLinkChips", () => {
  it("returns [] for undefined / empty attrs", () => {
    expect(buildLinkChips(undefined)).toEqual([]);
    expect(buildLinkChips({})).toEqual([]);
  });
  it("emits chips in the fixed order hsd -> jira -> pr -> url", () => {
    const chips = buildLinkChips({
      url:  "https://example.com",
      pr:   "o/r#7",
      jira: "ABC-1",
      hsd:  "1234567",
    });
    expect(chips.map(c => c.kind)).toEqual(["hsd", "jira", "pr", "url"]);
  });
  it("accepts array-valued attrs", () => {
    const chips = buildLinkChips({
      hsd: ["111", "222"],
      url: ["https://a.example.com", "https://b.example.com"],
    });
    expect(chips.map(c => c.label)).toEqual([
      "HSD-111", "HSD-222", "a.example.com", "b.example.com",
    ]);
  });
  it("skips invalid entries without failing others", () => {
    const chips = buildLinkChips({
      url: ["not a url", "https://good.example.com"],
      pr:  ["invalid", "owner/repo#42"],
    });
    expect(chips.map(c => c.kind)).toEqual(["pr", "url"]);
    expect(chips[1].href).toBe("https://good.example.com");
  });
});
