import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";

import { PARCELS_PMTILES_URL } from "../config/env";
import {
  HIGHLIGHT_COLOR,
  PARCELS_FILL_LAYER,
  PARCELS_HIGHLIGHT_LAYER,
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
      paint: { "fill-color": "#2563eb", "fill-opacity": 0.12 },
    });

    map.addLayer({
      id: PARCELS_LINE_LAYER,
      type: "line",
      source: PARCELS_SOURCE_ID,
      "source-layer": PARCELS_SOURCE_LAYER,
      paint: { "line-color": "#2563eb", "line-width": 0.6, "line-opacity": 0.7 },
    });

    // Selection highlight: a thick contrasting outline filtered to the selected parcel id.
    // Starts matching nothing (id = -1) until a parcel is selected.
    map.addLayer({
      id: PARCELS_HIGHLIGHT_LAYER,
      type: "line",
      source: PARCELS_SOURCE_ID,
      "source-layer": PARCELS_SOURCE_LAYER,
      filter: ["==", ["get", "id"], -1],
      paint: { "line-color": HIGHLIGHT_COLOR, "line-width": 2.5, "line-opacity": 0.95 },
    });
  } catch (err) {
    // Missing tiles / source-layer mismatch must not break the basemap render.
    console.warn("[parcels] failed to add layer", err);
  }
}

/** Highlight a parcel by id (or clear the highlight when id is null). */
export function setSelectedParcel(map: maplibregl.Map, id: number | string | null): void {
  if (!map.getLayer(PARCELS_HIGHLIGHT_LAYER)) return;
  map.setFilter(PARCELS_HIGHLIGHT_LAYER, ["==", ["get", "id"], id ?? -1]);
}
