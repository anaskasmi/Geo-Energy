// PLACEHOLDER build (GEO-1). Emits a static dist/index.html so the frontend→web_dist→web
// wiring can be exercised before the real Vite/MapLibre app exists (GEO-22).
import { mkdirSync, writeFileSync } from "node:fs";

mkdirSync("dist", { recursive: true });
writeFileSync(
  "dist/index.html",
  `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Site-Selection App — scaffold</title>
  </head>
  <body>
    <main style="font-family: system-ui; padding: 2rem">
      <h1>Site-Selection App</h1>
      <p>Frontend scaffold placeholder — the React + MapLibre SPA is built in GEO-22.</p>
    </main>
  </body>
</html>
`,
);
console.log("built dist/index.html (placeholder)");
