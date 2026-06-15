import { useMapStore } from "../map/useMapStore";
import type { DrawMode } from "../map/MapContext";

const SQ_METERS_PER_ACRE = 4046.8564224;

function formatArea(sqm: number | null): string {
  if (sqm == null) return "—";
  const acres = sqm / SQ_METERS_PER_ACRE;
  if (acres >= 1) return `${acres.toFixed(1)} ac`;
  return `${Math.round(sqm)} m²`;
}

/**
 * Floating drawing toolbar (GEO-23): draw / edit polygon, undo / redo (terra-draw built-in,
 * also Ctrl+Z / Ctrl+Y), clear, and a live turf area readout. Escape cancels an in-progress
 * polygon (terra-draw default). Clicking the active mode button toggles back to idle.
 */
export function DrawToolbar() {
  const {
    drawMode,
    setDrawMode,
    drawAreaSqm,
    canUndo,
    canRedo,
    canDeleteSelection,
    undo,
    redo,
    clearDraw,
    deleteSelection,
  } = useMapStore();

  const choose = (mode: DrawMode) => setDrawMode(drawMode === mode ? "idle" : mode);

  return (
    <div className="draw-toolbar" role="toolbar" aria-label="Drawing tools">
      <button
        type="button"
        className="draw-btn"
        aria-pressed={drawMode === "draw"}
        onClick={() => choose("draw")}
        title="Draw a polygon (Esc cancels)"
      >
        <span aria-hidden="true">▱</span> Draw
      </button>
      <button
        type="button"
        className="draw-btn"
        aria-pressed={drawMode === "edit"}
        onClick={() => choose("edit")}
        title="Edit: drag vertices, add midpoints, delete points"
      >
        <span aria-hidden="true">✎</span> Edit
      </button>
      <span className="draw-sep" aria-hidden="true" />
      <button
        type="button"
        className="draw-btn draw-btn--icon"
        onClick={undo}
        disabled={!canUndo}
        title="Undo (Ctrl+Z)"
        aria-label="Undo"
      >
        ↶
      </button>
      <button
        type="button"
        className="draw-btn draw-btn--icon"
        onClick={redo}
        disabled={!canRedo}
        title="Redo (Ctrl+Y)"
        aria-label="Redo"
      >
        ↷
      </button>
      <button
        type="button"
        className="draw-btn draw-btn--icon"
        onClick={deleteSelection}
        disabled={!canDeleteSelection}
        title="Delete selected polygon (Del) — select it in Edit mode first"
        aria-label="Delete selected polygon"
      >
        ✕
      </button>
      <button
        type="button"
        className="draw-btn draw-btn--icon"
        onClick={clearDraw}
        title="Clear all drawings"
        aria-label="Clear all drawings"
      >
        🗑
      </button>
      <span className="draw-area" aria-live="polite">
        Area: {formatArea(drawAreaSqm)}
      </span>
    </div>
  );
}
