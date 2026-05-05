import { test, expect, type Page, type APIRequestContext } from "@playwright/test";

/**
 * Caret-after-scroll regression — see issue #166 (umbrella #162, now closed).
 *
 * Repros the long-standing caret drift bug class (#52, #57, #155) in an
 * automated form against the CM6 editor (the only editor post-#162).
 * Workflow:
 *   1. Seed a 200-line note where line N reads `Line NNN: __MARKER__`.
 *   2. Scroll the editor to the bottom.
 *   3. Click on the target line (line 180 in the seeded body).
 *   4. Type the sentinel character `X`.
 *   5. Save (Ctrl/Cmd+S) and read the file back via the API.
 *   6. Assert the target line now reads `__XMARKER__`.  Any drift puts
 *      the X on a different line and fails the assertion.
 */

const FIXTURE_PROJECT = "__caret_drift__";
const FIXTURE_NOTE = `${FIXTURE_PROJECT}/large.md`;
const TOTAL_LINES = 200;
const TARGET_LINE = 180; // 1-based
const SENTINEL = "X";

function makeBody(): string {
  const lines: string[] = [];
  for (let i = 1; i <= TOTAL_LINES; i++) {
    lines.push(`Line ${String(i).padStart(3, "0")}: __MARKER__`);
  }
  return lines.join("\n") + "\n";
}

async function ensureProject(req: APIRequestContext) {
  const r = await req.post("/api/projects", { data: { name: FIXTURE_PROJECT } });
  if (![200, 201, 409].includes(r.status())) {
    throw new Error(`Could not create fixture project: ${r.status()} ${await r.text()}`);
  }
}

async function seedNote(req: APIRequestContext, body: string): Promise<string> {
  await ensureProject(req);
  const r = await req.put("/api/notes", {
    data: { path: FIXTURE_NOTE, body_md: body, if_match: "" },
  });
  if (!r.ok()) {
    throw new Error(`Could not seed fixture note: ${r.status()} ${await r.text()}`);
  }
  const json = (await r.json()) as { etag?: string };
  return json.etag ?? "";
}

async function readNote(req: APIRequestContext): Promise<string> {
  const list = await req.get("/api/notes");
  if (!list.ok()) throw new Error(`list failed: ${list.status()}`);
  const notes = (await list.json()) as { id: number; path: string }[];
  const me = notes.find((n) => n.path === FIXTURE_NOTE);
  if (!me) throw new Error(`fixture note ${FIXTURE_NOTE} not found in /api/notes`);
  const r = await req.get(`/api/notes/${me.id}`);
  if (!r.ok()) throw new Error(`read failed: ${r.status()} ${await r.text()}`);
  const json = (await r.json()) as { body_md: string };
  return json.body_md;
}

async function openNote(page: Page) {
  await page.goto("/");
  const link = page.getByRole("button", { name: /large\.md/i });
  await link.first().click();
  // Wait until the CM6 editor surface is visible.
  await expect(page.locator(".cm-content").first()).toBeVisible({ timeout: 10_000 });
}

async function scrollAndClickTarget(page: Page) {
  const editor = page.locator(".cm-content").first();
  await editor.evaluate((el) => {
    const scroller = (el as HTMLElement).closest(".cm-editor")?.querySelector(".cm-scroller")
      ?? (el as HTMLElement);
    (scroller as HTMLElement).scrollTop = (scroller as HTMLElement).scrollHeight;
  });
  await page
    .getByText(`Line ${String(TARGET_LINE).padStart(3, "0")}: __MARKER__`)
    .first()
    .click({ position: { x: 0, y: 0 } });
}

test.describe("caret fidelity — cm6", () => {
  test.beforeEach(async ({ request }) => {
    await seedNote(request, makeBody());
  });

  test("cm6 keeps caret on click after scroll (#164)", async ({ page, request }) => {
    await openNote(page);
    await scrollAndClickTarget(page);
    await page.keyboard.type(SENTINEL);
    await page.keyboard.press(process.platform === "darwin" ? "Meta+S" : "Control+S");
    await page.waitForTimeout(1500);
    const after = await readNote(request);
    const expected = `Line ${String(TARGET_LINE).padStart(3, "0")}: __${SENTINEL}MARKER__`;
    const lines = after.split("\n");
    expect(lines[TARGET_LINE - 1]).toBe(expected);
  });
});
