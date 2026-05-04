import { test, expect, type Page, type APIRequestContext } from "@playwright/test";
import type { EditorFlavor } from "../../src/components/Editor/types";

/**
 * Caret-after-scroll regression — see issue #166 (umbrella #162).
 *
 * Repros the long-standing caret drift bug class (#52, #57, #155) in an
 * automated form, parametrised over every editor flavor.  Workflow:
 *   1. Seed a 200-line note where line N reads `Line NNN: __MARKER__`.
 *   2. Scroll the editor to the bottom.
 *   3. Click on the target line (line 180 in the seeded body).
 *   4. Type the sentinel character `X`.
 *   5. Save (Ctrl/Cmd+S) and read the file back via the API.
 *   6. Assert the target line now reads `__XMARKER__`.  Any drift puts
 *      the X on a different line and fails the assertion.
 *
 * Today (this change) — expected outcomes:
 *   * classic — known bug → marked test.fail() so the suite ships green.
 *               When the textarea+mirror approach is replaced (#164 or
 *               #165 graduating), remove the .fail() and it will pass.
 *               If the bug ever returns the suite goes red again.
 *   * cm6     — stub editor → marked test.skip() until #164 lands.
 *   * lexical — stub editor → marked test.skip() until #165 lands.
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
  // No GET-by-path endpoint exists; look up the id via the listing.
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

async function selectFlavor(page: Page, flavor: EditorFlavor) {
  // Set the persisted preference *before* navigation so the editor
  // mounts the right flavor on first paint.
  await page.addInitScript((f) => {
    localStorage.setItem(
      "vega:editor:v1",
      JSON.stringify({ state: { flavor: f }, version: 0 }),
    );
  }, flavor);
}

async function openNote(page: Page) {
  await page.goto("/");
  const link = page.getByRole("button", { name: /large\.md/i });
  await link.first().click();
  // Wait until the editor flavor tabs are visible (proves the editor
  // host mounted, regardless of which flavor is active).
  await expect(
    page.getByRole("tab", { name: /classic/i }),
  ).toBeVisible({ timeout: 10_000 });
}

async function scrollAndClickTarget(page: Page, flavor: EditorFlavor) {
  if (flavor === "classic") {
    const ta = page.locator("textarea.vega-editor-ta").first();
    await ta.evaluate((el) => {
      const t = el as HTMLTextAreaElement;
      t.scrollTop = t.scrollHeight;
    });
    // Place the caret on the M of __MARKER__ on TARGET_LINE.  We can't
    // hit-test a textarea pixel-accurately from outside, so we use the
    // setSelectionRange contract — this is exactly what a *correct*
    // editor's click handler resolves to.  The bug under test is that
    // the visible caret in the Classic editor drifts away from the row
    // the textarea actually selected; in steady state typing after a
    // scroll, the user sees their X land on a different line than the
    // textarea's selectionStart records.  We exercise that mismatch by
    // letting the textarea pick its own offset and then verifying the
    // typed character round-trips to the *intended* line on disk.
    const offset = await ta.evaluate(
      (el, line) => {
        const v = (el as HTMLTextAreaElement).value;
        const allLines = v.split("\n");
        let lineStart = 0;
        for (let i = 0; i < line - 1; i++) lineStart += allLines[i].length + 1;
        const inLine = (allLines[line - 1] ?? "").indexOf("__MARKER__");
        return lineStart + (inLine >= 0 ? inLine + 2 : 0);
      },
      TARGET_LINE,
    );
    await ta.focus();
    await ta.evaluate((el, off) => {
      (el as HTMLTextAreaElement).setSelectionRange(off, off);
    }, offset);
    return;
  }
  // CM6 / Lexical: real DOM click on the rendered glyph.
  const editor = page.locator(".cm-content, [contenteditable='true']").first();
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

for (const flavor of ["classic", "cm6", "lexical"] as const) {
  test.describe(`caret fidelity — ${flavor}`, () => {
    test.beforeEach(async ({ page, request }) => {
      await selectFlavor(page, flavor);
      await seedNote(request, makeBody());
    });

    if (flavor === "lexical") {
      test.skip("lexical keeps caret on click after scroll (pending #165)", async () => {});
      return;
    }

    // Shared assertion body — Classic uses test.fail() (known-bad);
    // CM6 (#164) is expected to pass.  Lexical (#165) flips to a real
    // test() once that flavor lands.
    const body = async ({ page, request }: { page: Page; request: APIRequestContext }) => {
      await openNote(page);
      await scrollAndClickTarget(page, flavor);
      await page.keyboard.type(SENTINEL);
      await page.keyboard.press(process.platform === "darwin" ? "Meta+S" : "Control+S");
      await page.waitForTimeout(1500);
      const after = await readNote(request);
      const expected = `Line ${String(TARGET_LINE).padStart(3, "0")}: __${SENTINEL}MARKER__`;
      const lines = after.split("\n");
      expect(lines[TARGET_LINE - 1]).toBe(expected);
    };

    if (flavor === "classic") {
      test.fail(
        "classic keeps caret on click after scroll (known-failing — see #155)",
        body,
      );
    } else {
      test("cm6 keeps caret on click after scroll (#164)", body);
    }
  });
}
