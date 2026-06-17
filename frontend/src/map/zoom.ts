/**
 * Relative zoom math for the agent's `zoom_map` tool (text + voice).
 *
 * MapLibre's zoom is logarithmic — each +1 level doubles the on-screen magnification (and halves
 * the ground extent shown). The agent speaks in PERCENTAGES ("zoom out a bit" → ~15%), so we treat
 * the percent as a change in linear magnification and convert it to a zoom-LEVEL delta the map can
 * apply relative to wherever it is now:
 *   - in  P%  → magnify ×(1 + P/100)        → delta = +log2(1 + P/100)
 *   - out P%  → magnify ×1/(1 + P/100)       → delta = −log2(1 + P/100)
 * Defined as inverses so "zoom in 20%" then "zoom out 20%" round-trips to the original zoom.
 */

export type ZoomDirection = "in" | "out";

/** Clamp bounds for a single zoom step's percentage (mirrors the Python `zoom_map` clamp). */
export const ZOOM_PERCENT_MIN = 1;
export const ZOOM_PERCENT_MAX = 400;

/** Default step when the agent omits a percent ("zoom in" with no amount). */
export const ZOOM_PERCENT_DEFAULT = 15;

/** Convert a "zoom in/out by percent" request into a signed MapLibre zoom-level delta. */
export function zoomLevelDelta(direction: ZoomDirection, percent: number): number {
  const raw = Number.isFinite(percent) && percent > 0 ? percent : ZOOM_PERCENT_DEFAULT;
  const pct = Math.min(Math.max(raw, ZOOM_PERCENT_MIN), ZOOM_PERCENT_MAX);
  const factor = 1 + pct / 100;
  return direction === "out" ? -Math.log2(factor) : Math.log2(factor);
}
