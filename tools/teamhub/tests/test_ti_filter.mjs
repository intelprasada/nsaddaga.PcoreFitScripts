// Regression tests for the Turnins-tab filter logic in dashboard.html.
//
// Why these exist:
//   PR #352 (click a chart bar to filter turnins) introduced a bug where a
//   stale bucket filter would silently narrow the text-filter results — e.g.
//   searching "released,pmh" for Kelsey returned 0 hits instead of 5. There
//   were no automated tests, so it was only caught by a user. These tests
//   pin the intended behavior of tiMatchesOne / tiMatchesFilter and the
//   text-filter override of tiBucketFilter.
//
// Approach:
//   Extract the two pure functions and the tiFilter "input" listener body
//   verbatim from dashboard.html so we don't drift from what the browser
//   actually runs. Then assert against synthetic turnin objects.
//
// Run with:  node tools/teamhub/tests/test_ti_filter.mjs

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import assert from "node:assert/strict";

const HERE = dirname(fileURLToPath(import.meta.url));
const DASHBOARD = resolve(HERE, "..", "dashboard.html");
const html = readFileSync(DASHBOARD, "utf8");

// --- Extract function source by name -----------------------------------------
function extractFn(src, name) {
  const re = new RegExp("function\\s+" + name + "\\s*\\(([^)]*)\\)\\s*{", "g");
  const m = re.exec(src);
  if (!m) throw new Error(`function ${name} not found`);
  const start = m.index + m[0].length;
  let i = start, depth = 1;
  while (i < src.length && depth > 0) {
    const c = src[i++];
    if (c === "{") depth++;
    else if (c === "}") depth--;
  }
  return { args: m[1], body: src.slice(start, i - 1) };
}

const oneFn    = extractFn(html, "tiMatchesOne");
const filterFn = extractFn(html, "tiMatchesFilter");

const tiMatchesOne    = new Function(oneFn.args,    oneFn.body);
// tiMatchesFilter references tiMatchesOne by name; inject it via closure.
const tiMatchesFilter = new Function(
  "tiMatchesOne",
  `return function tiMatchesFilter(${filterFn.args}) { ${filterFn.body} };`
)(tiMatchesOne);

// --- Synthetic turnin fixtures -----------------------------------------------
const TI_KELSEY_PMH_RELEASED = {
  id: "7974",
  status: "released",
  stage: "final",
  cluster: "core/fe",
  project: "GFC",
  code_review_status: "approved",
  comments: "PMH emu signal cleanup",
  files_changed: [
    "core/fe/cte/fe_core/fe_core_cov.e",
    "core/fe/cte/pmh_emu/pmh_emu_sigs.e",
    "core/fe/cte/topology/fe_tlm_topology.e",
  ],
  hsds_added: ["12345678"],
  commits: [{ sha: "abcdef012345", subject: "pmh: rewire emu signals" }],
  turnin_time_epoch: 1_720_000_000,
};
const TI_RELEASED_NO_PMH = {
  id: "6001", status: "released", cluster: "core/be", project: "GFC",
  comments: "unrelated release", files_changed: ["core/be/foo.e"],
  commits: [{ sha: "111", subject: "foo" }],
  turnin_time_epoch: 1_720_500_000,
};
const TI_PMH_IN_FLIGHT = {
  id: "6100", status: "in flight", cluster: "core/fe", project: "GFC",
  comments: "pmh work still open", files_changed: ["core/fe/pmh/x.e"],
  commits: [{ sha: "222", subject: "wip" }],
  turnin_time_epoch: 1_721_000_000,
};
const TI_NOISE = {
  id: "9999", status: "cancelled", cluster: "core/mid", project: "JNC",
  comments: "nothing to see here", files_changed: ["core/mid/y.e"],
  commits: [{ sha: "333", subject: "noop" }],
  turnin_time_epoch: 1_721_500_000,
};
const CORPUS = [TI_KELSEY_PMH_RELEASED, TI_RELEASED_NO_PMH, TI_PMH_IN_FLIGHT, TI_NOISE];

// --- Tests --------------------------------------------------------------------
const tests = [];
const test = (name, fn) => tests.push({ name, fn });
const applyFilter = (q) => CORPUS.filter(t => tiMatchesFilter(t, q));

test("empty query returns everything", () => {
  assert.equal(applyFilter("").length, 4);
  assert.equal(applyFilter("   ").length, 4);
  assert.equal(applyFilter(null).length, 4);
});

test("single token searches comments", () => {
  assert.deepEqual(applyFilter("pmh").map(t=>t.id).sort(), ["6100","7974"]);
});

test("single token searches status", () => {
  assert.deepEqual(applyFilter("released").map(t=>t.id).sort(), ["6001","7974"]);
});

test("single token searches file paths", () => {
  assert.deepEqual(applyFilter("pmh_emu").map(t=>t.id), ["7974"]);
});

test("single token searches commit subjects (case-insensitive)", () => {
  assert.deepEqual(applyFilter("REWIRE").map(t=>t.id), ["7974"]);
});

test("single token searches commit sha prefix", () => {
  assert.deepEqual(applyFilter("abcdef").map(t=>t.id), ["7974"]);
});

test("single token searches HSDs", () => {
  assert.deepEqual(applyFilter("12345678").map(t=>t.id), ["7974"]);
});

test("comma-separated tokens AND together — the Kelsey regression", () => {
  const hits = applyFilter("released,pmh");
  assert.deepEqual(hits.map(t=>t.id), ["7974"],
    "released,pmh must match only the released TI whose files/comments mention pmh");
});

test("whitespace around commas is tolerated", () => {
  assert.deepEqual(applyFilter("released ,  pmh").map(t=>t.id), ["7974"]);
  assert.deepEqual(applyFilter(" ,pmh, ").map(t=>t.id).sort(), ["6100","7974"]);
});

test("case-insensitive tokens", () => {
  assert.deepEqual(applyFilter("RELEASED,PMH").map(t=>t.id), ["7974"]);
});

test("leading '-' negates a token", () => {
  assert.deepEqual(applyFilter("released,-pmh").map(t=>t.id), ["6001"]);
});

test("leading '!' also negates", () => {
  assert.deepEqual(applyFilter("released,!pmh").map(t=>t.id), ["6001"]);
});

test("all-negated tokens still filter correctly", () => {
  assert.deepEqual(applyFilter("-cancelled,-in flight").map(t=>t.id).sort(), ["6001","7974"]);
});

test("nonexistent token returns empty", () => {
  assert.deepEqual(applyFilter("nonesuchtoken").length, 0);
});

// --- Source-level tests for the tiBucketFilter interaction -------------------
// Pin the PR #352 bug fix so anyone who removes the clear line breaks a test.

test("tiFilter input listener clears stale tiBucketFilter", () => {
  const re = /getElementById\("tiFilter"\)\.addEventListener\("input",([\s\S]*?)\}\);/;
  const m = re.exec(html);
  assert.ok(m, "tiFilter input listener not found in dashboard.html");
  const body = m[1];
  assert.match(body, /tiBucketFilter\s*=\s*null/,
    "tiFilter input handler must reset tiBucketFilter to null");
  assert.match(body, /renderTiTable\s*\(/,
    "tiFilter input handler must call renderTiTable");
});

test("chart control listeners clear tiBucketFilter", () => {
  const re = /\["tiChartGran","tiChartSpan","tiChartStack"\]\.forEach\([\s\S]*?\}\);/;
  const m = re.exec(html);
  assert.ok(m, "chart control listener registration not found");
  assert.match(m[0], /clearTiBucketFilter|tiBucketFilter\s*=\s*null/,
    "control-change listeners must clear the sticky bucket filter");
});

test("loadTurnins resets tiBucketFilter after successful fetch", () => {
  const re = /tiData\s*=\s*await\s+r\.json\(\)\s*;\s*tiBucketFilter\s*=\s*null\s*;/;
  assert.match(html, re,
    "loadTurnins must reset tiBucketFilter after fetching new data");
});

// --- Runner -------------------------------------------------------------------
let failed = 0;
for (const {name, fn} of tests) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
  } catch (e) {
    failed++;
    console.error(`  ✗ ${name}`);
    console.error(`    ${e.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed ? 1 : 0);
