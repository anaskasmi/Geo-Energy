import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { useMediaQuery } from "../hooks/useMediaQuery";
import { ThemeContext } from "./ThemeContext";
import type { ThemeContextValue } from "./ThemeContext";
import { PREFERS_DARK_QUERY, THEME_STORAGE_KEY } from "./theme";
import type { ResolvedTheme, ThemePreference } from "./theme";

function readStoredPreference(): ThemePreference {
  if (typeof localStorage === "undefined") return "system";
  const stored = localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === "light" || stored === "dark" || stored === "system") return stored;
  return "system";
}

/**
 * Provides theme state to the app:
 * - defaults to "system" (follows the OS), with light/dark also selectable
 * - persists the choice to localStorage
 * - applies `data-theme` + `color-scheme` to <html> so CSS variables and native UI
 *   (scrollbars, form controls) follow the theme
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference);
  const systemPrefersDark = useMediaQuery(PREFERS_DARK_QUERY);

  const resolvedTheme: ResolvedTheme = useMemo(() => {
    if (preference === "system") return systemPrefersDark ? "dark" : "light";
    return preference;
  }, [preference, systemPrefersDark]);

  // Reflect the resolved theme on the document root for CSS + native UI.
  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute("data-theme", resolvedTheme);
    root.style.colorScheme = resolvedTheme;
  }, [resolvedTheme]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // localStorage may be unavailable (private mode); theme still works in-session.
    }
  }, []);

  const toggle = useCallback(() => {
    setPreference(resolvedTheme === "dark" ? "light" : "dark");
  }, [resolvedTheme, setPreference]);

  const value: ThemeContextValue = useMemo(
    () => ({ preference, resolvedTheme, setPreference, toggle }),
    [preference, resolvedTheme, setPreference, toggle],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
