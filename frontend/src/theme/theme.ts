/** Theme types and shared constants. */

/** What the user can choose: an explicit theme, or "follow the system". */
export type ThemePreference = "light" | "dark" | "system";

/** The actual rendered theme after resolving "system" against the OS preference. */
export type ResolvedTheme = "light" | "dark";

/** localStorage key for the persisted preference. Matches the inline script in index.html. */
export const THEME_STORAGE_KEY = "geo-energy-theme";

/** Media query used to detect the OS dark-mode preference. */
export const PREFERS_DARK_QUERY = "(prefers-color-scheme: dark)";
