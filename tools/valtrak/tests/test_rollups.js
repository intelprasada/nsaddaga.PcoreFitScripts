const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(require.resolve("../app.js"), "utf8");
const between = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const context = { structuralSubtypes: new Set(["TCD", "TPF"]) };
vm.runInNewContext([
  between("function statusCounts", "function targetGap"),
  between("function itemSupportsSectionTarget", "function resolvedTarget"),
  between("function itemHasStatus", "function normalizedPlanCandidates"),
  between("function functionalType", "function loadTypeInclusions"),
  between("function buildTree", "function renderTree"),
  "this.rollups = { buildTree, buildHierarchyRollups, assertHierarchyIntegrity,"
    + " visibleHierarchyNodes, hierarchyItemVisible, functionalType,"
    + " validationMilestone, itemHasStatus };",
].join("\n"), context);

const {
  buildTree,
  buildHierarchyRollups,
  assertHierarchyIntegrity,
  visibleHierarchyNodes,
  hierarchyItemVisible,
  functionalType,
  validationMilestone,
  itemHasStatus,
} = context.rollups;

const root = { p: "Plan/RTL Assertions", st: "TCD", t: "" };
const section = {
  p: `${root.p}/First section`,
  k: "Reference",
  s: "open",
  t: "",
};
const items = [root, section];
for (let index = 0; index < 3; index += 1) {
  items.push({
    p: `${section.p}/complete-${index}`,
    st: "TC",
    s: "complete",
    t: "Design_requirement",
    mil: index === 0 ? "VAL1.0" : "VAL2.0",
  });
}
for (let index = 0; index < 8; index += 1) {
  items.push({
    p: `${section.p}/open-${index}`,
    st: "TC",
    s: "open",
    t: "Design_requirement",
    mil: index < 4 ? "VAL1.0" : "VAL2.0",
  });
}
const internal = {
  p: `${section.p}/nested-design-requirement`,
  st: "TC",
  s: "open",
  t: "Design_requirement",
};
items.push(
  internal,
  { p: `${internal.p}/coverage`, st: "TC", s: "open", t: "Coverage" },
  { p: `${section.p}/coverage-1`, st: "TC", s: "open", t: "Coverage" },
  { p: `${section.p}/coverage-2`, st: "TC", s: "open", t: "Coverage" },
);

const roots = buildTree(items);
const allRollups = buildHierarchyRollups(roots);
assertHierarchyIntegrity(items, roots, allRollups);
assert.deepStrictEqual(
  ["complete", "open", "active"].map((key) => allRollups.get(section.p)[key]),
  [3, 12, 15],
);
assert.deepStrictEqual(
  ["complete", "open", "active"].map((key) => allRollups.get(internal.p)[key]),
  [0, 2, 2],
);
assert.strictEqual(itemHasStatus(section), false);

const included = new Set(["Coverage"]);
const includeCoverage = (item) => included.has(functionalType(item));
const coverageRollups = buildHierarchyRollups(roots, includeCoverage);
assertHierarchyIntegrity(
  items,
  roots,
  coverageRollups,
  includeCoverage,
  (item) => hierarchyItemVisible(item, included),
);
assert.deepStrictEqual(
  ["complete", "open", "active"].map((key) => coverageRollups.get(section.p)[key]),
  [0, 3, 3],
);

function visibleStatusItems(nodes) {
  return visibleHierarchyNodes(nodes, included).flatMap((node) => [
    ...(itemHasStatus(node.item) ? [node.item] : []),
    ...visibleStatusItems(node.children),
  ]);
}
const visible = visibleStatusItems(roots);
assert.strictEqual(visible.length, 3);
assert.ok(visible.every((item) => item.t === "Coverage"));

const includedTypes = new Set(["Design_requirement"]);
const includedMilestones = new Set(["VAL1.0"]);
const includeVal1Design = (item) =>
  includedTypes.has(functionalType(item))
  && includedMilestones.has(validationMilestone(item));
const milestoneRollups = buildHierarchyRollups(roots, includeVal1Design);
assertHierarchyIntegrity(
  items,
  roots,
  milestoneRollups,
  includeVal1Design,
  (item) => hierarchyItemVisible(item, includedTypes, includedMilestones),
);
assert.deepStrictEqual(
  ["complete", "open", "active"].map((key) => milestoneRollups.get(section.p)[key]),
  [1, 4, 5],
);

assert.throws(
  () => buildTree([root, { ...root }]),
  /missing or duplicate path/,
);

console.log("Hierarchy rollup integrity assertions passed");
