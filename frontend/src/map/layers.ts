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

import {
  FLOOD_FILL_LAYER,
  FLOOD_LINE_LAYER,
  PARCELS_FILL_LAYER,
  PARCELS_LINE_LAYER,
  SUBSTATIONS_CIRCLE_LAYER,
  TRANSMISSION_CASING_LAYER,
  TRANSMISSION_LINE_LAYER,
} from "./constants";

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

export const LAYERS: LayerDef[] = [
  {
    id: "parcels",
    label: "Parcels",
    swatch: "#3b5bdb",
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
    swatch: "#f08c00",
    symbol: "line",
    available: true,
    mapLayers: [
      { id: TRANSMISSION_CASING_LAYER, opacityProp: "line-opacity", baseOpacity: 0.35 },
      { id: TRANSMISSION_LINE_LAYER, opacityProp: "line-opacity", baseOpacity: 0.9 },
    ],
    defaultVisible: true,
    defaultOpacity: 1,
  },
  {
    id: "substations",
    label: "Substations",
    swatch: "#e8368f",
    symbol: "circle",
    available: true,
    mapLayers: [{ id: SUBSTATIONS_CIRCLE_LAYER, opacityProp: "circle-opacity", baseOpacity: 0.95 }],
    defaultVisible: true,
    defaultOpacity: 1,
  },
  {
    id: "sfha",
    label: "Flood (SFHA)",
    swatch: "#22b8cf",
    symbol: "fill",
    available: true,
    mapLayers: [
      { id: FLOOD_FILL_LAYER, opacityProp: "fill-opacity", baseOpacity: 0.22 },
      { id: FLOOD_LINE_LAYER, opacityProp: "line-opacity", baseOpacity: 0.8 },
    ],
    defaultVisible: true,
    defaultOpacity: 1,
  },
  {
    id: "result",
    label: "Suitability score",
    swatch: "#22a884",
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

/**
 * Low → high suitability color ramp for the legend + the score overlay.
 *
 * GEO-28: this is **viridis** — perceptually uniform (equal steps look equally different) and
 * color-vision-deficiency safe. We deliberately do NOT use a red→green ramp: red/green is the
 * most common CVD confusion, and a diverging ramp also misreads a single-ended "suitability"
 * scale. Viridis is monotonic in lightness (dark=low → bright=high), so the order survives
 * greyscale printing and every CVD type. Score is never conveyed by colour ALONE — the ranked
 * list, the numeric score chip, and the rank pair with it everywhere.
 *
 * Upgraded to 10 stops (design system §4.2) for a smoother sRGB lerp that better preserves
 * viridis's perceptual uniformity across the 0–100 range.
 */
export const SCORE_RAMP = [
  "#440154",
  "#482878",
  "#3e4a89",
  "#31688e",
  "#26828e",
  "#1f9e89",
  "#35b779",
  "#6dcd59",
  "#b4de2c",
  "#fde725",
];

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

function _srgbToLinear(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

/** Readable text color for a score chip: pick black or white by WHICHEVER gives the higher WCAG
 *  contrast ratio against the chip background (not a naive luminance threshold — viridis mid-greens
 *  are dark enough that white text fails AA, so they need black). Always lands ≥ ~4.6:1 on viridis. */
export function scoreTextColor(score: number | null | undefined): string {
  const [r, g, b] = scoreColor(score, 255);
  const L = 0.2126 * _srgbToLinear(r) + 0.7152 * _srgbToLinear(g) + 0.0722 * _srgbToLinear(b);
  const contrastWhite = 1.05 / (L + 0.05);
  const contrastBlack = (L + 0.05) / 0.05;
  return contrastBlack >= contrastWhite ? "#1a1a1a" : "#ffffff";
}

/**
 * Friendly layer names the assistant (echoing the user) might use -> canonical layer id. Mirrors
 * the Python `_LAYER_ALIASES` (api/app/agent_tools.py) so the voice `set_map_view` tool resolves
 * the same vocabulary the text agent does. Used only by the voice executor (the text agent resolves
 * names server-side and relays canonical ids).
 */
const LAYER_ALIASES: Record<string, string> = {
  parcels: "parcels", parcel: "parcels", lots: "parcels", lot: "parcels",
  transmission: "transmission", "transmission line": "transmission",
  "transmission lines": "transmission", "power line": "transmission",
  "power lines": "transmission", powerline: "transmission", powerlines: "transmission",
  lines: "transmission", grid: "transmission", "grid lines": "transmission",
  substation: "substations", substations: "substations", subs: "substations", sub: "substations",
  sfha: "sfha", flood: "sfha", floods: "sfha", flooding: "sfha", "flood zone": "sfha",
  "flood zones": "sfha", floodplain: "sfha", "flood plain": "sfha", "flood hazard": "sfha",
  "flood (sfha)": "sfha", fema: "sfha",
  result: "result", results: "result", score: "result", scores: "result", scoring: "result",
  suitability: "result", "suitability score": "result", "suitability scores": "result",
  "scored parcels": "result", heatmap: "result", ranking: "result", rankings: "result",
};
const ALL_LAYER_WORDS = new Set(["all", "everything", "every layer", "all layers", "every"]);
const NO_LAYER_WORDS = new Set(["none", "nothing", "no layers", "no layer"]);

/**
 * Resolve a comma/semicolon list of friendly layer names to canonical ids (de-duped, in input
 * order), plus any tokens that didn't resolve. 'all'/'everything' expands to every layer; 'none'
 * resolves to nothing.
 */
export function resolveLayerNames(text: string): { ids: string[]; unknown: string[] } {
  const ids: string[] = [];
  const unknown: string[] = [];
  for (const raw of String(text ?? "").split(/[,;/]+/)) {
    const tok = raw.trim().toLowerCase().replace(/\s+/g, " ").replace(/^[.\s]+|[.\s]+$/g, "");
    if (!tok) continue;
    if (ALL_LAYER_WORDS.has(tok)) {
      for (const def of LAYERS) if (!ids.includes(def.id)) ids.push(def.id);
      continue;
    }
    if (NO_LAYER_WORDS.has(tok)) continue;
    const id = LAYER_ALIASES[tok];
    if (!id) {
      unknown.push(raw.trim());
      continue;
    }
    if (!ids.includes(id)) ids.push(id);
  }
  return { ids, unknown };
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
