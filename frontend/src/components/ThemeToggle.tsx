import { Moon, Sun } from "lucide-react";

import { useTheme } from "../theme/useTheme";
import { Icon } from "./Icon";

/**
 * Compact theme toggle: a single icon button that flips light ↔ dark. It shows the CURRENT
 * theme's glyph (sun in light, moon in dark); the aria-label / title state the action. Tapping
 * sets an explicit preference (overriding "system"). Floats in the map's top-left corner on both
 * the desktop and mobile shells, so it stays a small, out-of-the-way control instead of the old
 * three-segment row that collided with the drawing toolbar.
 */
export function ThemeToggle() {
  const { resolvedTheme, setPreference } = useTheme();
  const isDark = resolvedTheme === "dark";
  const next = isDark ? "light" : "dark";
  return (
    <button
      type="button"
      className="theme-toggle"
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      onClick={() => setPreference(next)}
    >
      <Icon icon={isDark ? Sun : Moon} size={18} />
    </button>
  );
}
