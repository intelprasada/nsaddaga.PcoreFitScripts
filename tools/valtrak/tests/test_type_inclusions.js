const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(require.resolve("../app.js"), "utf8");
const between = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const storage = {
  "valtrak-plan-types-v1": JSON.stringify({
    IFU_FV_Unit_Vplan: ["Unclassified"],
  }),
};
const items = [
  { t: "" },
  { t: "Test" },
  { t: "Checker" },
  { t: "Coverage" },
];
const state = {
  items,
  projectIncludedTypes: new Set(),
  planIncludedTypes: new Map(),
  planKnownTypes: new Map(),
};
const context = {
  state,
  localStorage: {
    getItem: (key) => storage[key] ?? null,
    setItem: (key, value) => { storage[key] = value; },
  },
};

vm.runInNewContext([
  between("function functionalType", "function renderTypeInclusionControl"),
  "this.types = { loadTypeInclusions, planTypeSelection, savePlanTypeInclusions };",
].join("\n"), context);

const { loadTypeInclusions, planTypeSelection, savePlanTypeInclusions } = context.types;
loadTypeInclusions();
let included = planTypeSelection("IFU_FV_Unit_Vplan", items);
assert.deepStrictEqual([...included].sort(), ["Checker", "Coverage", "Test", "Unclassified"]);

included.delete("Checker");
savePlanTypeInclusions();
state.planIncludedTypes.clear();
state.planKnownTypes.clear();
loadTypeInclusions();
included = planTypeSelection("IFU_FV_Unit_Vplan", items);
assert.deepStrictEqual([...included].sort(), ["Coverage", "Test", "Unclassified"]);

items.push({ t: "Newly discovered" });
included = planTypeSelection("IFU_FV_Unit_Vplan", items);
assert.deepStrictEqual(
  [...included].sort(),
  ["Coverage", "Newly discovered", "Test", "Unclassified"],
);

console.log("Plan type inclusion migration assertions passed");
