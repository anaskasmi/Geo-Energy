import type { Map as MapLibreMap } from "maplibre-gl";

import { API_BASE_URL } from "../config/env";
import {
  FLOOD_FILL_LAYER,
  FLOOD_LINE_LAYER,
  FLOOD_SOURCE_ID,
  SUBSTATIONS_CIRCLE_LAYER,
  SUBSTATIONS_SOURCE_ID,
  TRANSMISSION_CASING_LAYER,
  TRANSMISSION_LINE_LAYER,
  TRANSMISSION_SOURCE_ID,
} from "./constants";

/**
 * Static map overlay layers — transmission lines, substations, flood (SFHA). Each is a MapLibre
 * `geojson` source fed directly by the `/api/layer/<name>` endpoint (MapLibre fetches the URL; an
 * absent/empty layer just renders nothing, so this degrades gracefully before those tables are
 * ingested). The Layers panel (map/layers.ts) toggles visibility + opacity through applyLayerState,
 * which references the layer ids in constants.ts.
 *
 * Colors are the design-system §4.3 categorical palette — CVD-safe and legible over the light,
 * dark, AND satellite basemaps: amber lines (dark casing), magenta substations (white stroke),
 * translucent cyan flood fill (+ outline). Added once on style.load (idempotent), after the
 * parcels layers so infrastructure sits on top; the deck.gl scored overlay stays above everything.
 */

const TRANSMISSION_COLOR = "#f08c00";
const SUBSTATION_COLOR = "#e8368f";
const FLOOD_FILL_COLOR = "#22b8cf";
const FLOOD_OUTLINE_COLOR = "#1098ad";

function layerUrl(name: string): string {
  return `${API_BASE_URL}/layer/${name}`;
}

export function addOverlayLayers(map: MapLibreMap): void {
  // Flood (SFHA): translucent fill + outline, added first so it sits beneath the line/point layers.
  if (!map.getSource(FLOOD_SOURCE_ID)) {
    map.addSource(FLOOD_SOURCE_ID, { type: "geojson", data: layerUrl("flood") });
    map.addLayer({
      id: FLOOD_FILL_LAYER,
      type: "fill",
      source: FLOOD_SOURCE_ID,
      paint: { "fill-color": FLOOD_FILL_COLOR, "fill-opacity": 0.22 },
    });
    map.addLayer({
      id: FLOOD_LINE_LAYER,
      type: "line",
      source: FLOOD_SOURCE_ID,
      paint: { "line-color": FLOOD_OUTLINE_COLOR, "line-width": 1, "line-opacity": 0.8 },
    });
  }

  // Transmission lines: a dark casing under an amber line so the route reads on any basemap.
  if (!map.getSource(TRANSMISSION_SOURCE_ID)) {
    map.addSource(TRANSMISSION_SOURCE_ID, { type: "geojson", data: layerUrl("transmission") });
    map.addLayer({
      id: TRANSMISSION_CASING_LAYER,
      type: "line",
      source: TRANSMISSION_SOURCE_ID,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#000000", "line-width": 3, "line-opacity": 0.35 },
    });
    map.addLayer({
      id: TRANSMISSION_LINE_LAYER,
      type: "line",
      source: TRANSMISSION_SOURCE_ID,
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": TRANSMISSION_COLOR, "line-width": 1.5, "line-opacity": 0.9 },
    });
  }

  // Substations: magenta circles with a white stroke (reads on white, black, and imagery alike).
  if (!map.getSource(SUBSTATIONS_SOURCE_ID)) {
    map.addSource(SUBSTATIONS_SOURCE_ID, { type: "geojson", data: layerUrl("substations") });
    map.addLayer({
      id: SUBSTATIONS_CIRCLE_LAYER,
      type: "circle",
      source: SUBSTATIONS_SOURCE_ID,
      paint: {
        "circle-color": SUBSTATION_COLOR,
        "circle-radius": 4,
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 1.5,
        "circle-opacity": 0.95,
      },
    });
  }
}
