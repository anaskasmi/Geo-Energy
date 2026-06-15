import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Design system fonts (self-hosted, variable weight axis only — ~47KB latin each).
// The family strings are "Inter Variable" / "JetBrains Mono Variable" (see --font-* in global.css).
import "@fontsource-variable/inter/wght.css";
import "@fontsource-variable/jetbrains-mono/wght.css";

import "maplibre-gl/dist/maplibre-gl.css";
import "./styles/global.css";
import "./styles/components.css";

import App from "./App";
import { ThemeProvider } from "./theme/ThemeProvider";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error('Root element "#root" not found');
}

createRoot(rootElement).render(
  <StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </StrictMode>,
);
