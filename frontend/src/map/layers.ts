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
    available: false,
    mapLayers: [],
    defaultVisible: true,
    defaultOpacity: 1,
    note: SOON,
  },
];

/** Low → high suitability color ramp for the legend (and the future score layer). */
export const SCORE_RAMP = ["#d73027", "#fc8d59", "#fee08b", "#91cf60", "#1a9850"];

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
