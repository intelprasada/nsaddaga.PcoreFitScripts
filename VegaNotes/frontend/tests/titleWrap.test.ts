import { describe, it, expect } from "vitest";
import { splitTitleForWrap } from "../src/lib/titleWrap";

describe("splitTitleForWrap (#273)", () => {
  it("returns single-element array for empty/plain titles", () => {
    expect(splitTitleForWrap("")).toEqual([""]);
    expect(splitTitleForWrap("Simple task")).toEqual(["Simple task"]);
  });

  it("splits on :: preserving delimiter", () => {
    expect(splitTitleForWrap("fe::Assert::T_IDQ")).toEqual([
      "fe", "::", "Assert", "::", "T", "_", "IDQ",
    ]);
  });

  it("splits on _ and . and -", () => {
    expect(splitTitleForWrap("foo_bar.baz-qux")).toEqual([
      "foo", "_", "bar", ".", "baz", "-", "qux",
    ]);
  });

  it("splits on /", () => {
    expect(splitTitleForWrap("path/to/thing")).toEqual([
      "path", "/", "to", "/", "thing",
    ]);
  });

  it("handles real RTL regression bucket title", () => {
    const t = "EID::1686927::IDQ_WR_EMRN_VALID_MISMATCH";
    const parts = splitTitleForWrap(t);
    // joining without delimiters must reconstruct original
    expect(parts.join("")).toBe(t);
    // has multiple wrap opportunities
    expect(parts.length).toBeGreaterThan(4);
  });

  it("handles real FPV assertion title with no whitespace", () => {
    const t = "fe::Assert::T_IDQ_RAT_FPV_Relamination_correctness_stimuli_1::msid_idq_fv_IDQ_RAT_intf_chk_inst_idq_rat_assume_uop_loop_genblk::gfc-a0";
    const parts = splitTitleForWrap(t);
    expect(parts.join("")).toBe(t);
    // Every delimiter yields a wrap point → the browser can break at ::
    // and _ boundaries instead of mid-word.
    expect(parts.length).toBeGreaterThan(20);
  });

  it("round-trips arbitrary input (join === original)", () => {
    const samples = [
      "abc",
      "abc::def",
      "a_b_c",
      "no-delims-just-hyphens",
      "mix::of_types.and-things/here",
      "",
    ];
    for (const s of samples) {
      expect(splitTitleForWrap(s).join("")).toBe(s);
    }
  });

  it("__ (double underscore) split treated as a single delimiter", () => {
    // We list __ before _ in the regex alternation so it wins the match.
    const parts = splitTitleForWrap("foo__bar");
    expect(parts).toEqual(["foo", "__", "bar"]);
  });
});
