import { useTheme } from "../theme/useTheme";
import type { ThemePreference } from "../theme/theme";

const OPTIONS: { value: ThemePreference; label: string; icon: string }[] = [
  { value: "light", label: "Light", icon: "☀" },
  { value: "system", label: "System", icon: "◐" },
  { value: "dark", label: "Dark", icon: "☾" },
];

/**
 * Theme selector: Light / System / Dark. "System" follows the OS preference; Light/Dark
 * are manual overrides. The choice is persisted by the ThemeProvider.
 */
export function ThemeToggle() {
  const { preference, setPreference } = useTheme();

  return (
    <div className="theme-toggle" role="group" aria-label="Color theme">
      {OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          className="theme-toggle__btn"
          aria-pressed={preference === option.value}
          onClick={() => setPreference(option.value)}
          title={`${option.label} theme`}
        >
          <span aria-hidden="true">{option.icon}</span>
          <span className="theme-toggle__label">{option.label}</span>
        </button>
      ))}
    </div>
  );
}
