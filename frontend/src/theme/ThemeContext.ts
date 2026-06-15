import { createContext } from "react";

import type { ResolvedTheme, ThemePreference } from "./theme";

export interface ThemeContextValue {
  /** The user's choice: "light", "dark", or "system". */
  preference: ThemePreference;
  /** The actual theme in effect after resolving "system". */
  resolvedTheme: ResolvedTheme;
  /** Set an explicit preference (persisted to localStorage). */
  setPreference: (preference: ThemePreference) => void;
  /** Convenience: flip between light and dark (drops "system"). */
  toggle: () => void;
}

export const ThemeContext = createContext<ThemeContextValue | null>(null);
