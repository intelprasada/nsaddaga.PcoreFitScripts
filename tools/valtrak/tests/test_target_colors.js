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
  "this.targets = { completionTargetMet, targetMetClass };",
].join("\n"), context);

const { completionTargetMet, targetMetClass } = context.targets;
assert.strictEqual(completionTargetMet({ active: 10, complete: 8 }, "overall"), true);
assert.strictEqual(completionTargetMet({ active: 10, complete: 7 }, "overall"), false);
assert.strictEqual(completionTargetMet({ active: 4, complete: 3 }, "plan", "Plan A"), true);
assert.strictEqual(
  completionTargetMet({ active: 2, complete: 1 }, "section", "Plan A::section-1"),
  true,
);
assert.strictEqual(completionTargetMet({ active: 0, complete: 0 }, "overall"), false);
assert.strictEqual(targetMetClass({ active: 10, complete: 8 }, "overall"), "is-target-met");
assert.strictEqual(targetMetClass({ active: 10, complete: 7 }, "overall"), "");

console.log("Completion target color assertions passed");
