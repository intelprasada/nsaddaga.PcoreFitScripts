/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Vitest config — kept separate from `vite.config.ts` so the dev/build
// pipeline stays untouched.  Notable bits:
//   * environment: jsdom — pre-existing tests (editor / sidebar prefs)
//     read `localStorage` directly; without jsdom they explode in node.
//   * exclude: drop `tests/e2e/**` so Playwright specs don't get scooped
//     up by vitest's `**/*.spec.ts` discovery (see #166).
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["tests/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["tests/e2e/**", "node_modules/**", "dist/**"],
  },
});
