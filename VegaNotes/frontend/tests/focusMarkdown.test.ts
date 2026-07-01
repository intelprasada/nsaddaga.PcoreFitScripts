/** Unit tests for the FocusBanner inline markdown renderer (#266). */
import { describe, it, expect } from "vitest";
import { renderFocusMarkdown } from "../src/components/FocusBanner/focusMarkdown";

describe("renderFocusMarkdown", () => {
  it("returns empty for empty/whitespace input", () => {
    expect(renderFocusMarkdown("")).toBe("");
    expect(renderFocusMarkdown("   \n  ")).toBe("");
  });

  it("wraps a single paragraph in <p>", () => {
    expect(renderFocusMarkdown("Hello team")).toBe("<p>Hello team</p>");
  });

  it("renders **bold** and *italic*", () => {
    const html = renderFocusMarkdown("Ship by **Wed EOD** *no excuses*");
    expect(html).toContain("<strong>Wed EOD</strong>");
    expect(html).toContain("<em>no excuses</em>");
  });

  it("renders inline `code`", () => {
    const html = renderFocusMarkdown("Lock down `release/main`");
    expect(html).toContain("<code");
    expect(html).toContain("release/main");
  });

  it("renders [label](url) links and adds rel=noopener", () => {
    const html = renderFocusMarkdown("See [the doc](https://example.com/x)");
    expect(html).toContain('href="https://example.com/x"');
    expect(html).toContain("the doc</a>");
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("autolinks bare http(s) URLs", () => {
    const html = renderFocusMarkdown("Plan at https://example.com/plan");
    expect(html).toContain('href="https://example.com/plan"');
  });

  it("rejects javascript: links and keeps them literal", () => {
    const html = renderFocusMarkdown("[click](javascript:alert(1))");
    expect(html).not.toContain("href=\"javascript:");
    expect(html).toContain("[click]");
  });

  it("escapes raw HTML to prevent injection", () => {
    const html = renderFocusMarkdown("<script>alert(1)</script>");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });

  it("splits on blank lines into multiple <p>", () => {
    const html = renderFocusMarkdown("First para.\n\nSecond para.");
    const pCount = (html.match(/<p>/g) ?? []).length;
    expect(pCount).toBe(2);
  });

  it("preserves single newlines as <br />", () => {
    const html = renderFocusMarkdown("line one\nline two");
    expect(html).toContain("<br />");
  });
});
