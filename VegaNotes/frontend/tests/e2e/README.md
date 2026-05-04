# End-to-end tests (Playwright)

These tests drive the live frontend (`vite`, port 5173) and backend
(`uvicorn`, port 8000) — start the dev stack first, then run the suite.

```sh
# 1. (one time) install browsers
npx playwright install chromium

# 2. run the suite
npm run e2e
```

Auth defaults to HTTP Basic `admin / admin`.  If your dev box has had
the admin password rotated, override via env:

```sh
VEGA_USER=admin VEGA_PASS='your-pass' npm run e2e
```

Other env knobs (see `playwright.config.ts`):

| var            | default                  | meaning                  |
|----------------|--------------------------|--------------------------|
| VEGA_BASE_URL  | http://localhost:5173    | where to point the tests |
| VEGA_USER      | admin                    | basic-auth username      |
| VEGA_PASS      | admin                    | basic-auth password      |

Each suite seeds its own fixtures under a dedicated project so they
never collide with your real notes.

## Suites

* **caret-drift.spec.ts** — Issue #166 / umbrella #162.  Repros the
  caret-after-scroll bug class (#52, #57, #155) for every editor flavor
  (Classic / CM6 / Lexical).  Classic is marked `test.fail()` until the
  bug is fixed/retired; CM6 + Lexical are skipped until #164 / #165
  land.
