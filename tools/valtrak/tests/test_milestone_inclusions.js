const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(require.resolve("../app.js"), "utf8");
const between = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const storage = {};
const items = [
  { mil: "" },
  { mil: "VAL1.0" },
  { mil: "VAL2.0" },
];
const state = {
  items,
  planIncludedMilestones: new Map(),
  planKnownMilestones: new Map(),
};
const context = {
  state,
  localStorage: {
    getItem: (key) => storage[key] ?? null,
    setItem: (key, value) => { storage[key] = value; },
  },
};

vm.runInNewContext([
  between("function validationMilestone", "function loadTypeInclusions"),
  between("function loadMilestoneInclusions", "function renderInclusionControl"),
  "this.milestones = { loadMilestoneInclusions, planMilestoneSelection,"
    + " savePlanMilestoneInclusions, validationMilestone };",
].join("\n"), context);

const {
  loadMilestoneInclusions,
  planMilestoneSelection,
  savePlanMilestoneInclusions,
  validationMilestone,
} = context.milestones;

assert.strictEqual(validationMilestone(items[0]), "No milestone");
loadMilestoneInclusions();
let included = planMilestoneSelection("Plan A", items);
assert.deepStrictEqual([...included].sort(), ["No milestone", "VAL1.0", "VAL2.0"]);

included.delete("VAL1.0");
savePlanMilestoneInclusions();
state.planIncludedMilestones.clear();
state.planKnownMilestones.clear();
loadMilestoneInclusions();
included = planMilestoneSelection("Plan A", items);
assert.deepStrictEqual([...included].sort(), ["No milestone", "VAL2.0"]);

items.push({ mil: "VAL3.0" });
included = planMilestoneSelection("Plan A", items);
assert.deepStrictEqual([...included].sort(), ["No milestone", "VAL2.0", "VAL3.0"]);

items.splice(1, 1);
included = planMilestoneSelection("Plan A", items);
savePlanMilestoneInclusions();
state.planIncludedMilestones.clear();
state.planKnownMilestones.clear();
loadMilestoneInclusions();
items.push({ mil: "VAL1.0" });
included = planMilestoneSelection("Plan A", items);
assert.deepStrictEqual([...included].sort(), ["No milestone", "VAL2.0", "VAL3.0"]);

console.log("Plan milestone inclusion assertions passed");
