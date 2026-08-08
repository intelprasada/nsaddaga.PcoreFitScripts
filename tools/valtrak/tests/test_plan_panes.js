const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(require.resolve("../app.js"), "utf8");
const between = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const elements = {
  "#plan-search": { value: "" },
  "#plan-count-label": {},
  "#selected-plan-count-label": {},
  "#plan-list": {},
  "#selected-plan-list": {},
};
const state = {
  plans: [
    { vplan_name: "Plan B", owner: "Bob" },
    { vplan_name: "Plan A", owner: "Alice" },
    { vplan_name: "Plan C", owner: "Chris" },
  ],
  planStats: new Map(),
  monitoredPlans: new Set(["Plan A", "Plan C"]),
  selectedPlan: "Plan C",
};
let savedPlan = "Plan B";
const context = {
  state,
  localStorage: {
    getItem: () => savedPlan,
    setItem: (_, value) => { savedPlan = value; },
  },
  $: (selector) => elements[selector],
  formatNumber: String,
  escapeAttribute: String,
  escapeHtml: String,
  statusCounts: () => ({}),
  planIncludedItems: () => [],
  completionLabel: () => "",
  targetSummary: () => "",
};

vm.runInNewContext([
  between("function lastOpenPlan", "function functionalType"),
  between("function planSort", "function renderPlanDetail"),
  "this.planPanes = { lastOpenPlan, rememberOpenPlan, renderPlanList };",
].join("\n"), context);

const { lastOpenPlan, rememberOpenPlan, renderPlanList } = context.planPanes;
assert.strictEqual(lastOpenPlan("Plan A"), "Plan B");
savedPlan = "Removed Plan";
assert.strictEqual(lastOpenPlan("Plan A"), "Plan A");
rememberOpenPlan("Plan C");
assert.strictEqual(savedPlan, "Plan C");

renderPlanList();
assert.match(elements["#selected-plan-list"].innerHTML, /Plan A/);
assert.match(elements["#selected-plan-list"].innerHTML, /Plan C/);
assert.doesNotMatch(elements["#selected-plan-list"].innerHTML, /Plan B/);
assert.match(elements["#plan-list"].innerHTML, /Plan A/);
assert.match(elements["#plan-list"].innerHTML, /Plan B/);
assert.match(elements["#plan-list"].innerHTML, /Plan C/);
assert.strictEqual(elements["#selected-plan-count-label"].textContent, "2");
assert.strictEqual(
  (elements["#selected-plan-list"].innerHTML.match(/is-selected/g) || []).length,
  1,
);
assert.strictEqual(
  (elements["#plan-list"].innerHTML.match(/is-selected/g) || []).length,
  1,
);

console.log("Selected plan pane and persistence assertions passed");
