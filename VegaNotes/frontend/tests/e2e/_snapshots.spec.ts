import { test, expect, type Page } from "@playwright/test";
import * as fs from "fs";

/**
 * UI snapshot harness (not a regression test — a screenshot generator).
 *
 * Captures full-page PNGs of every major view for before/after UX review.
 * Output dir is configurable via SHOT_DIR (defaults to ./_shots).
 *
 * Run with:
 *   SHOT_DIR=/path/to/out VEGA_USER=shots VEGA_PASS=shots123 \
 *     npx playwright test tests/e2e/_snapshots.spec.ts --project=chromium
 */

const OUT = process.env.SHOT_DIR ?? "./_shots";
fs.mkdirSync(OUT, { recursive: true });

// Primary views live inline in the appbar; secondary views live in "More".
const PRIMARY: { view: string; label: RegExp }[] = [
  { view: "my-tasks", label: /^My Tasks$/ },
  { view: "editor", label: /^Editor$/ },
  { view: "kanban", label: /^Kanban$/ },
  { view: "agenda", label: /^Agenda$/ },
  { view: "timeline", label: /^Timeline$/ },
  { view: "calendar", label: /^Calendar$/ },
  { view: "graph", label: /^Graph$/ },
];
const SECONDARY: { view: string; label: RegExp }[] = [
  { view: "archive", label: /^Archive$/ },
  { view: "dashboard", label: /^Dashboard$/ },
  { view: "me", label: /^Me$/ },
  { view: "help", label: /^Help$/ },
];

async function settle(page: Page, ms = 900) {
  await page.waitForTimeout(ms);
}

async function shot(page: Page, view: string) {
  await settle(page);
  await page.screenshot({ path: `${OUT}/${view}.png`, fullPage: true });
  // eslint-disable-next-line no-console
  console.log(`shot: ${view}`);
}

test("capture all views", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/");
  await expect(page.getByText("VegaNotes").first()).toBeVisible({ timeout: 15_000 });
  await settle(page, 1500);

  for (const { view, label } of PRIMARY) {
    try {
      await page.getByRole("button", { name: label }).first().click({ timeout: 8_000 });
      await shot(page, view);
    } catch (e) {
      console.log(`SKIP ${view}: ${(e as Error).message.split("\n")[0]}`);
    }
  }

  for (const { view, label } of SECONDARY) {
    try {
      await page.getByRole("button", { name: /^More$/ }).first().click({ timeout: 8_000 });
      await settle(page, 300);
      await page.getByRole("menuitem", { name: label }).first().click({ timeout: 8_000 });
      await shot(page, view);
    } catch (e) {
      console.log(`SKIP ${view}: ${(e as Error).message.split("\n")[0]}`);
    }
  }

  // Command palette overlay
  try {
    await page.getByRole("button", { name: /^My Tasks$/ }).first().click();
    await settle(page, 400);
    await page.keyboard.press(process.platform === "darwin" ? "Meta+K" : "Control+K");
    await settle(page, 500);
    await page.screenshot({ path: `${OUT}/command-palette.png`, fullPage: false });
    await page.keyboard.press("Escape");
  } catch (e) {
    console.log(`SKIP palette: ${(e as Error).message.split("\n")[0]}`);
  }
});
