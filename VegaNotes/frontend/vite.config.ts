import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws":  { target: "ws://localhost:8000", ws: true },
      "/healthz": "http://localhost:8000",
    },
  },
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
