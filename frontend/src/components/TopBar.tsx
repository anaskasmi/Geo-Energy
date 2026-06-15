import { DrawToolbar } from "./DrawToolbar";
import { MapControls } from "./MapControls";

/**
 * Full-width floating top bar over the map. Left: the drawing tools. Right: the single-purpose
 * control buttons (Assistant / Scoring / Layers / Share / Settings / Theme), each opening its own
 * minimal popover. One bar, two clusters — keeps every control on the map and the panels off the
 * sidebar (which no longer exists; the only docked surface is the Results panel).
 */
export function TopBar({ onStartTour }: { onStartTour?: () => void }) {
  return (
    <div className="topbar overlay-panel">
      <DrawToolbar />
      <MapControls onStartTour={onStartTour} />
    </div>
  );
}
