import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server and `vite preview` proxy /api, /ws and /healthz to the
// backend. Both the listen port and the backend target are env-overridable so
// dev and prod can run side-by-side on swapped ports without cross-wiring —
// e.g. prod on :5173/:8000 (the shared team URL) and dev on :4173/:8100.
// Defaults preserve the original single-instance behaviour (:5173 → :8000).
const BACKEND_PORT = process.env.VEGA_BACKEND_PORT ?? "8000";
const FRONTEND_PORT = Number(process.env.VEGA_FRONTEND_PORT ?? "5173");
const backend = `http://localhost:${BACKEND_PORT}`;
const proxy = {
  "/api": backend,
  "/ws": { target: `ws://localhost:${BACKEND_PORT}`, ws: true },
  "/healthz": backend,
};

// Hostnames the dev/preview server will answer to, so the app can be reached
// by name (e.g. http://sccc06443708.sc.intel.com:5173) instead of a bare IP.
// A leading-dot entry matches that domain and all subdomains, so ".intel.com"
// covers every corp host. Override with VEGA_ALLOWED_HOSTS (comma-separated).
const allowedHosts = (process.env.VEGA_ALLOWED_HOSTS ?? ".intel.com,localhost")
  .split(",")
  .map((h) => h.trim())
  .filter(Boolean);

export default defineConfig({
  plugins: [react()],
  server: { port: FRONTEND_PORT, host: true, allowedHosts, proxy },
  // `vite preview` (used by prod-start.sh) needs its own proxy block so the
  // built bundle's /api calls reach the prod backend, not whatever is on :8000.
  preview: { port: FRONTEND_PORT, host: true, allowedHosts, proxy },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Split the stable vendor core into its own long-cache chunk. The
        // heavy view-only libraries (CodeMirror, FullCalendar, ReactFlow,
        // Chart.js) are already isolated via React.lazy dynamic imports, so
        // they don't need to be listed here — Rollup emits a chunk per lazy
        // boundary automatically.
        manualChunks: {
          "vendor-react": ["react", "react-dom", "react-router-dom"],
          "vendor-query": ["@tanstack/react-query", "zustand"],
        },
      },
    },
  },
});
