import { useContext } from "react";

import { ThemeContext } from "./ThemeContext";
import type { ThemeContextValue } from "./ThemeContext";

/** Access the current theme state. Must be used within a <ThemeProvider>. */
export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return ctx;
}
