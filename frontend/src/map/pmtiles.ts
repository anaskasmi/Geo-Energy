import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";

import { PARCELS_PMTILES_URL } from "../config/env";
import {
  HIGHLIGHT_INNER_COLOR,
  HIGHLIGHT_INNER_WIDTH,
  HIGHLIGHT_OUTER_COLOR,
  HIGHLIGHT_OUTER_WIDTH,
  PARCELS_COLOR,
  PARCELS_FILL_LAYER,
  PARCELS_HIGHLIGHT_LAYER,
  PARCELS_HIGHLIGHT_OUTER_LAYER,
  PARCELS_LINE_LAYER,
  PARCELS_SOURCE_ID,
  PARCELS_SOURCE_LAYER,
} from "./constants";

let registered = false;

/**
 * Registers the `pmtiles://` protocol with MapLibre so vector sources can be served
 * directly from a single .pmtiles archive over HTTP byte-range requests. Idempotent —
 * safe to call on every map mount (incl. React StrictMode double-invoke).
 */
export function registerPmtilesProtocol(): void {
  if (registered) return;
  const protocol = new Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  registered = true;
}

/**
 * Adds the parcels vector source + fill/line/highlight layers to the map's current style.
 *
 * Idempotent: returns early if the source already exists. The basemap style is swapped on
 * theme/basemap change (which wipes custom sources/layers), so this is re-run on every
 * `style.load`. Wrapped in try/catch so a missing/404 .pmtiles never hard-crashes the app —
 * the basemap still renders.
 */
export function addParcelsLayer(map: maplibregl.Map): void {
  if (map.getSource(PARCELS_SOURCE_ID)) return;
  try {
    map.addSource(PARCELS_SOURCE_ID, {
      type: "vector",
      url: `pmtiles://${PARCELS_PMTILES_URL}`,
    });

    map.addLayer({
      id: PARCELS_FILL_LAYER,
      type: "fill",
      source: PARCELS_SOURCE_ID,
      "source-layer": PARCELS_SOURCE_LAYER,
      paint: { "fill-color": PARCELS_COLOR, "fill-opacity": 0.1 },
    });

    map.addLayer({
      id: PARCELS_LINE_LAYER,
      type: "line",
      source: PARCELS_SOURCE_ID,
      "source-layer": PARCELS_SOURCE_LAYER,
      paint: { "line-color": PARCELS_COLOR, "line-width": 0.75, "line-opacity": 0.75 },
    });

    // Selection highlight: a hue-free DOUBLE CASING filtered to the selected parcel id (§4.4).
    // Outer white line (wider) is added first so the inner near-black line draws on top of it,
    // producing a white halo that reads on any basemap. Both start matching nothing (id = -1).
    map.addLayer({
      id: PARCELS_HIGHLIGHT_OUTER_LAYER,
      type: "line",
      source: PARCELS_SOURCE_ID,
      "source-layer": PARCELS_SOURCE_LAYER,
      filter: ["==", ["get", "id"], -1],
      layout: { "line-join": "round" },
      paint: { "line-color": HIGHLIGHT_OUTER_COLOR, "line-width": HIGHLIGHT_OUTER_WIDTH, "line-opacity": 0.95 },
    });
    map.addLayer({
      id: PARCELS_HIGHLIGHT_LAYER,
      type: "line",
      source: PARCELS_SOURCE_ID,
      "source-layer": PARCELS_SOURCE_LAYER,
      filter: ["==", ["get", "id"], -1],
      layout: { "line-join": "round" },
      paint: { "line-color": HIGHLIGHT_INNER_COLOR, "line-width": HIGHLIGHT_INNER_WIDTH, "line-opacity": 1 },
    });
  } catch (err) {
    // Missing tiles / source-layer mismatch must not break the basemap render.
    console.warn("[parcels] failed to add layer", err);
  }
}

/** Highlight a parcel by id (or clear the highlight when id is null). Updates both casing layers. */
export function setSelectedParcel(map: maplibregl.Map, id: number | string | null): void {
  const filter: maplibregl.FilterSpecification = ["==", ["get", "id"], id ?? -1];
  if (map.getLayer(PARCELS_HIGHLIGHT_OUTER_LAYER)) map.setFilter(PARCELS_HIGHLIGHT_OUTER_LAYER, filter);
  if (map.getLayer(PARCELS_HIGHLIGHT_LAYER)) map.setFilter(PARCELS_HIGHLIGHT_LAYER, filter);
}
