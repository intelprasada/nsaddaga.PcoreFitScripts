import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { parse, parseEta, parseDuration, parsePriorityRank } from "../src/index.ts";

const here = dirname(fileURLToPath(import.meta.url));
const fixturesDir = join(here, "fixtures");

for (const f of readdirSync(fixturesDir)) {
  if (!f.endsWith(".md")) continue;
  const stem = f.replace(/\.md$/, "");
  test(`golden:${stem}`, () => {
    const md = readFileSync(join(fixturesDir, `${stem}.md`), "utf8");
    const expected = JSON.parse(readFileSync(join(fixturesDir, `${stem}.json`), "utf8"));
    const got = parse(md);
    assert.deepEqual(got, expected);
  });
}

test("eta iso", () => assert.equal(parseEta("2026-05-01"), "2026-05-01"));
test("eta relative", () =>
  assert.equal(parseEta("+3d", new Date(Date.UTC(2026, 3, 19))), "2026-04-22"));
test("eta words", () => {
  const today = new Date(Date.UTC(2026, 3, 19)); // Sunday
  assert.equal(parseEta("today", today), "2026-04-19");
  assert.equal(parseEta("tomorrow", today), "2026-04-20");
  assert.equal(parseEta("next mon", today), "2026-04-20");
  assert.equal(parseEta("next fri", today), "2026-04-24");
});
test("eta invalid", () => assert.equal(parseEta("not a date"), null));
test("duration", () => {
  assert.equal(parseDuration("4h"), 4);
  assert.equal(parseDuration("1d"), 8);
  assert.equal(parseDuration("0.5w"), 20);
  assert.equal(parseDuration("bad"), null);
});
test("priority", () => {
  assert.equal(parsePriorityRank("P0"), 0);
  assert.equal(parsePriorityRank("P1"), 1);
  assert.equal(parsePriorityRank("low"), 7);
  assert.equal(parsePriorityRank("???"), 999);
});
