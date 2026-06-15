import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite config for the Site-Selection SPA (GEO-22).
// Production multi-stage Docker build + build-arg env injection is GEO-33; this config
// just builds to `dist/` (consumed by the frontend Dockerfile) and runs the dev server.
export default defineConfig({
  plugins: [react()],
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
