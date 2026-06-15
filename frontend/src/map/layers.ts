/**
 * Map layer registry (GEO-26): the single source of truth for the toggle/opacity controls
 * and the legend. Each entry maps a logical layer (what the user sees in the Layers panel)
 * to the concrete MapLibre layer ids it controls.
 *
 * Only `parcels` has a client-side tile source today (the PMTiles archive, GEO-14). The
 * other layers (transmission, substations, SFHA, scoring result) are declared here so the
 * control surface + legend are complete and ready, but are marked `available: false` until
 * their tiles/scores are produced (GEO-16+/GEO-24+) — they render as disabled rows rather
 * than faking data.
 */

import type { Map as MapLibreMap } from "maplibre-gl";

import { PARCELS_FILL_LAYER, PARCELS_LINE_LAYER } from "./constants";

/** A concrete MapLibre layer controlled by a logical layer, with its opacity paint prop. */
export interface MapLayerRef {
  id: string;
  opacityProp: "fill-opacity" | "line-opacity" | "circle-opacity";
  /** Opacity rendered when the slider is at 1.0 (the layer's design opacity). */
  baseOpacity: number;
}

export type LayerSymbol = "fill" | "line" | "circle" | "ramp";

export interface LayerDef {
  id: string;
  label: string;
  swatch: string;
  symbol: LayerSymbol;
  /** Whether a client-side source exists today (false → disabled control). */
  available: boolean;
  mapLayers: MapLayerRef[];
  defaultVisible: boolean;
  /** Opacity slider value in [0,1] (a multiplier on each map layer's baseOpacity). */
  defaultOpacity: number;
  note?: string;
}

const SOON = "Appears here once its tiles/scores are produced (GEO-16+/GEO-24+).";

export const LAYERS: LayerDef[] = [
  {
    id: "parcels",
    label: "Parcels",
    swatch: "#2563eb",
    symbol: "fill",
    available: true,
    mapLayers: [
      { id: PARCELS_FILL_LAYER, opacityProp: "fill-opacity", baseOpacity: 0.12 },
      { id: PARCELS_LINE_LAYER, opacityProp: "line-opacity", baseOpacity: 0.7 },
    ],
    defaultVisible: true,
    defaultOpacity: 1,
  },
  {
    id: "transmission",
    label: "Transmission lines",
    swatch: "#f59e0b",
    symbol: "line",
    available: false,
    mapLayers: [],
    defaultVisible: true,
    defaultOpacity: 1,
    note: SOON,
  },
  {
    id: "substations",
    label: "Substations",
    swatch: "#ef4444",
    symbol: "circle",
    available: false,
    mapLayers: [],
    defaultVisible: true,
    defaultOpacity: 1,
    note: SOON,
  },
  {
    id: "sfha",
    label: "Flood (SFHA)",
    swatch: "#38bdf8",
    symbol: "fill",
    available: false,
    mapLayers: [],
    defaultVisible: true,
    defaultOpacity: 1,
    note: SOON,
  },
  {
    id: "result",
    label: "Suitability score",
    swatch: "#1a9850",
    symbol: "ramp",
    // Rendered by the deck.gl overlay (GEO-24), not a native MapLibre layer — so mapLayers is
    // empty and MapView reads this toggle's visibility/opacity to drive the overlay directly.
    available: true,
    mapLayers: [],
    defaultVisible: true,
    defaultOpacity: 0.85,
  },
];

/** Logical layer id for the deck.gl scored-parcels overlay (GEO-24). */
export const RESULT_LAYER_ID = "result";

/** Low → high suitability color ramp for the legend + the score overlay. */
export const SCORE_RAMP = ["#d73027", "#fc8d59", "#fee08b", "#91cf60", "#1a9850"];

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const SCORE_RAMP_RGB = SCORE_RAMP.map(hexToRgb);

/** Interpolate the score ramp (0..100) to an RGBA color for the deck.gl overlay. */
export function scoreColor(score: number | null | undefined, alpha = 200): [number, number, number, number] {
  const t = Math.max(0, Math.min(1, (score ?? 0) / 100));
  const seg = t * (SCORE_RAMP_RGB.length - 1);
  const i = Math.min(SCORE_RAMP_RGB.length - 2, Math.floor(seg));
  const f = seg - i;
  const [ar, ag, ab] = SCORE_RAMP_RGB[i];
  const [br, bg, bb] = SCORE_RAMP_RGB[i + 1];
  return [
    Math.round(ar + (br - ar) * f),
    Math.round(ag + (bg - ag) * f),
    Math.round(ab + (bb - ab) * f),
    alpha,
  ];
}

/** Same ramp as a CSS color string (for the results list score chips). */
export function scoreColorCss(score: number | null | undefined): string {
  const [r, g, b] = scoreColor(score, 255);
  return `rgb(${r}, ${g}, ${b})`;
}

/** Readable text color (near-black or white) for a score chip, by background luminance (WCAG). */
export function scoreTextColor(score: number | null | undefined): string {
  const [r, g, b] = scoreColor(score, 255);
  const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  return luminance > 0.6 ? "#1a1a1a" : "#ffffff";
}

export interface LayerToggleState {
  visible: boolean;
  opacity: number;
}
export type LayerStateMap = Record<string, LayerToggleState>;

export function initialLayerState(): LayerStateMap {
  const state: LayerStateMap = {};
  for (const def of LAYERS) {
    state[def.id] = { visible: def.defaultVisible, opacity: def.defaultOpacity };
  }
  return state;
}

/**
 * Apply the current toggle/opacity state to the map. Idempotent and safe to call on every
 * change and after a style reload (skips layers not yet present on the map).
 */
export function applyLayerState(map: MapLibreMap, state: LayerStateMap): void {
  for (const def of LAYERS) {
    const toggle = state[def.id];
    if (!toggle) continue;
    for (const ml of def.mapLayers) {
      if (!map.getLayer(ml.id)) continue;
      map.setLayoutProperty(ml.id, "visibility", toggle.visible ? "visible" : "none");
      map.setPaintProperty(ml.id, ml.opacityProp, ml.baseOpacity * toggle.opacity);
    }
  }
}
