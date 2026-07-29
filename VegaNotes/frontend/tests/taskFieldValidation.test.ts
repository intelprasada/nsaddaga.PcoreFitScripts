import { describe, it, expect } from "vitest";
import { validateProgress, validateNoSpaceCsv } from "../src/lib/taskFieldValidation";

describe("validateProgress", () => {
  it("accepts empty (clears the token)", () => {
    expect(validateProgress("")).toBeNull();
    expect(validateProgress("   ")).toBeNull();
  });
  it("accepts N, N/D, and N/D label", () => {
    expect(validateProgress("42")).toBeNull();
    expect(validateProgress("30/54")).toBeNull();
    expect(validateProgress("30/54 fixed")).toBeNull();
    expect(validateProgress("3/10 day-1")).toBeNull();
  });
  it("rejects malformed shapes", () => {
    expect(validateProgress("abc")).toMatch(/Use N/);
    expect(validateProgress("30 / 54")).toMatch(/Use N/);
    expect(validateProgress("30/54 2fixed")).toMatch(/Use N/);
  });
  it("rejects a zero denominator (mirrors backend)", () => {
    expect(validateProgress("3/0")).toMatch(/greater than 0/);
  });
});

describe("validateNoSpaceCsv", () => {
  it("accepts empty and clean CSV items", () => {
    expect(validateNoSpaceCsv("", "HSD")).toBeNull();
    expect(validateNoSpaceCsv("1234567", "HSD")).toBeNull();
    expect(validateNoSpaceCsv("ABC-42, XYZ-9", "JIRA")).toBeNull();
    expect(validateNoSpaceCsv("owner/repo#42, o/r#7", "PR")).toBeNull();
  });
  it("flags an item that contains whitespace", () => {
    expect(validateNoSpaceCsv("ABC 42", "JIRA")).toMatch(/can't contain spaces/);
    expect(validateNoSpaceCsv("good, bad key", "JIRA")).toMatch(/JIRA values/);
  });
  it("ignores surrounding whitespace around separators", () => {
    expect(validateNoSpaceCsv("  1234567 ,  2345678 ", "HSD")).toBeNull();
  });
});
