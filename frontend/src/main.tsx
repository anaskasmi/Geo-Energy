import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

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
