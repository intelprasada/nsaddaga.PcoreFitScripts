import { test } from "@playwright/test";
import * as fs from "fs";
const OUT = process.env.SHOT_DIR ?? "./_shots";
fs.mkdirSync(OUT, { recursive: true });

test("popover", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/");
  await page.waitForTimeout(1500);
  await page.getByRole("button", { name: /^Kanban$/ }).first().click();
  await page.waitForTimeout(1000);

  // Pick a project that has tasks via the FilterBar project dropdown.
  const selects = page.locator("select");
  // second select is "all projects"
  try { await selects.nth(1).selectOption({ label: "#FIT Val Weekly" }); } catch { /* */ }
  await page.waitForTimeout(1200);

  let cards = page.locator("div.card.cursor-pointer");
  let n = await cards.count();
  console.log("kanban cards:", n);
  if (n === 0) {
    // try other projects
    const opts = await selects.nth(1).locator("option").allTextContents();
    for (const o of opts.slice(1)) {
      await selects.nth(1).selectOption({ label: o });
      await page.waitForTimeout(900);
      n = await page.locator("div.card.cursor-pointer").count();
      if (n > 0) { console.log("found tasks in", o); break; }
    }
    cards = page.locator("div.card.cursor-pointer");
  }
  if (await cards.count() > 0) {
    await cards.first().click({ position: { x: 40, y: 12 } });
    await page.waitForTimeout(900);
    if (await page.locator("#task-edit-form").count() > 0) {
      await page.screenshot({ path: `${OUT}/popover.png`, fullPage: false });
      console.log("captured popover");
      return;
    }
  }
  console.log("popover did not open");
  await page.screenshot({ path: `${OUT}/kanban-state.png`, fullPage: true });
});
