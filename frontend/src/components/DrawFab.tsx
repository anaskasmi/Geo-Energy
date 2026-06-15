import { useMapStore } from "../map/useMapStore";
import { haptic } from "../utils/haptics";

/**
 * Mobile floating action button (GEO-29): a thumb-zone primary action to start/stop drawing a
 * search area. Sits above the bottom sheet's peek height and inside the safe-area insets. On
 * desktop the equivalent lives in the floating draw toolbar + the "D" shortcut.
 */
export function DrawFab() {
  const { drawMode, setDrawMode } = useMapStore();
  const active = drawMode === "draw";
  return (
    <button
      type="button"
      className="fab"
      aria-pressed={active}
      aria-label={active ? "Stop drawing" : "Draw a search area"}
      onClick={() => {
        haptic(12);
        setDrawMode(active ? "idle" : "draw");
      }}
    >
      <span aria-hidden="true">✏️</span>
      {active ? "Drawing…" : "Draw"}
    </button>
  );
}
