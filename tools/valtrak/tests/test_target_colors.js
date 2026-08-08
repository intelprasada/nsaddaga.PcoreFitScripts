const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(require.resolve("../app.js"), "utf8");
const between = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const state = {
  completionTargets: {
    overall: 80,
    plans: { "Plan A": 75 },
    sections: { "Plan A::section-1": 50 },
  },
  items: [
    { cp: "Plan A", id: "section-1" },
  ],
  planOverviewName: "",
  selectedPlan: "",
};
const context = {
  state,
  structuralSubtypes: new Set(["TCD", "TPF"]),
  formatNumber: String,
};

vm.runInNewContext([
  between("function targetGap", "function targetControl"),
  "this.targets = { completionTargetMet, targetStatusClass, rollupStatusClass };",
].join("\n"), context);

const { completionTargetMet, targetStatusClass, rollupStatusClass } = context.targets;
assert.strictEqual(completionTargetMet({ active: 10, complete: 8 }, "overall"), true);
assert.strictEqual(completionTargetMet({ active: 10, complete: 7 }, "overall"), false);
assert.strictEqual(completionTargetMet({ active: 4, complete: 3 }, "plan", "Plan A"), true);
assert.strictEqual(
  completionTargetMet({ active: 2, complete: 1 }, "section", "Plan A::section-1"),
  true,
);
assert.strictEqual(completionTargetMet({ active: 0, complete: 0 }, "overall"), false);
assert.strictEqual(targetStatusClass({ active: 10, complete: 8, completion: 0.8 }, "overall"), "is-target-met");
assert.strictEqual(targetStatusClass({ active: 10, complete: 7, completion: 0.7 }, "overall"), "is-target-near");
assert.strictEqual(targetStatusClass({ active: 10, complete: 6, completion: 0.6 }, "overall"), "is-target-low");
assert.strictEqual(targetStatusClass({ active: 0, complete: 0, completion: 0 }, "overall"), "");
assert.strictEqual(rollupStatusClass({ st: "TC", s: "complete" }, {}), "is-status-complete");
assert.strictEqual(rollupStatusClass({ st: "TC", s: "open" }, {}), "is-status-open");
assert.strictEqual(rollupStatusClass({ st: "TC", s: "future" }, {}), "");
assert.strictEqual(
  rollupStatusClass(
    { st: "TCD", cp: "Plan A", id: "section-1" },
    { active: 2, complete: 1, completion: 0.5 },
  ),
  "is-target-met",
);
assert.match(
  source,
  /plan-list-target \$\{targetStatusClass\(counts, "plan", plan\.vplan_name\)\}/,
);

console.log("Completion target color assertions passed");
