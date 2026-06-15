import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { FontaineTransform } from "fontaine";

// Vite config for the Site-Selection SPA (GEO-22).
// Production multi-stage Docker build + build-arg env injection is GEO-33; this config
// just builds to `dist/` (consumed by the frontend Dockerfile) and runs the dev server.
export default defineConfig({
  plugins: [
    react(),
    // Design system: generate metric-matched fallback @font-faces for our web fonts so the
    // system-font first paint occupies the same box as Inter/JetBrains Mono — near-zero layout
    // shift (CLS) when the variable fonts swap in (the side panels are number-dense). The
    // fallback's family name is the *next* family in our --font-* stacks (system-ui / ui-monospace).
    FontaineTransform.vite({
      fallbacks: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica", "Arial", "sans-serif"],
      resolvePath: (id) => new URL("." + id, import.meta.url),
    }),
  ],
  // Pre-bundle the icon library in dev so MapLibre/deck-heavy pages don't pay a 5000-module
  // cold-start; named barrel imports from lucide-react tree-shake fine in the prod build.
  optimizeDeps: {
    include: ["lucide-react"],
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  server: {
    port: 5173,
    // Dev convenience: proxy the API base path to a locally-running api service so the
    // SPA's `/api` calls work in `vite dev`. Harmless if nothing is listening (requests
    // simply fail). The FastAPI app serves its routes UNDER `/api` (e.g. /api/health), so
    // forward the path unchanged — do NOT strip the prefix. In prod, nginx (GEO-34) serves `/api`.
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
