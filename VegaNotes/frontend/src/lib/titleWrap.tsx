// Long task titles from RTL / verification workflows often have no
// whitespace at all — e.g.
//
//   EID::1686927::IDQ_WR_EMRN_VALID_MISMATCH (4) -- ...
//   fe::Assert::T_IDQ_RAT_FPV_Relamination_correctness_stimuli_1::gfc-a0
//
// These would either overflow the card horizontally or force the
// browser to break at arbitrary character positions. Insert soft-break
// hints after common delimiters (::, _, ., -, /) so the browser
// prefers to wrap / truncate at semantically meaningful boundaries.
//
// Kept as a pure string-splitter for easy unit testing; the React
// wrapper below interleaves <wbr /> elements between the segments.
import React from "react";

const DELIM_RE = /(::|__|_|\.|-|\/)/g;

export function splitTitleForWrap(title: string): string[] {
  if (!title) return [""];
  const parts = title.split(DELIM_RE).filter((p) => p !== "");
  return parts.length > 0 ? parts : [title];
}

export function TitleWithBreakHints({ text }: { text: string }): React.ReactElement {
  const parts = splitTitleForWrap(text);
  return (
    <>
      {parts.map((p, i) => (
        <React.Fragment key={i}>
          {p}
          {i < parts.length - 1 && <wbr />}
        </React.Fragment>
      ))}
    </>
  );
}
