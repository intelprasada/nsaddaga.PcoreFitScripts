const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(require.resolve("../app.js"), "utf8");
const start = source.indexOf("async function queueBulkStatusJobs");
const end = source.indexOf("function enqueueStatusJob");
const context = {};
vm.runInNewContext(
  `${source.slice(start, end)}\nthis.queueBulkStatusJobs = queueBulkStatusJobs;`,
  context,
);

(async () => {
  const items = [
    { p: "Plan/A", n: "A", s: "open" },
    { p: "Plan/B", n: "B", s: "open" },
    { p: "Plan/C", n: "C", s: "complete" },
  ];
  const attempted = [];
  const result = await context.queueBulkStatusJobs(items, "complete", async (item) => {
    attempted.push(item.n);
    if (item.n === "B") throw new Error("stale status");
    return { id: `job-${item.n}` };
  });

  assert.deepStrictEqual(attempted, ["A", "B"]);
  assert.strictEqual(result.queued.length, 1);
  assert.strictEqual(result.queued[0].job.id, "job-A");
  assert.strictEqual(result.unchanged.length, 1);
  assert.strictEqual(result.unchanged[0].n, "C");
  assert.strictEqual(result.failed.length, 1);
  assert.strictEqual(result.failed[0].item.n, "B");
  assert.strictEqual(result.failed[0].error, "stale status");
  console.log("Bulk status queue assertions passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
