import { useMapStore } from "../map/useMapStore";
import type { BasemapId } from "../theme/basemap";

const OPTIONS: { id: BasemapId; label: string }[] = [
  { id: "auto", label: "Auto" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
  { id: "streets", label: "Streets" },
  { id: "satellite", label: "Satellite" },
];

/**
 * Basemap selector (GEO-26). "Auto" follows the app theme (light/dark); the others pin a
 * specific basemap. All are token-less and data-muted so overlays read clearly.
 */
export function BasemapControl() {
  const { basemap, setBasemap } = useMapStore();
  return (
    <div className="segmented" role="group" aria-label="Basemap">
      {OPTIONS.map((option) => (
        <button
          key={option.id}
          type="button"
          className="segmented__btn"
          aria-pressed={basemap === option.id}
          onClick={() => setBasemap(option.id)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
