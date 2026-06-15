/**
 * Map-related constants for the Site-Selection SPA.
 *
 * Serving CRS is EPSG:4326 (lon/lat degrees) per docs/CONVENTIONS.md — MapLibre uses
 * [lng, lat] order. The default view is Kern County, CA.
 */

/** Default map center, [lng, lat] (Kern County, CA). */
export const MAP_CENTER: [number, number] = [-118.7, 35.4];

/** Default zoom level. */
export const MAP_ZOOM = 8;

/** MapLibre source id for the parcels vector tiles. */
export const PARCELS_SOURCE_ID = "parcels";

/**
 * Source-layer name inside the parcels PMTiles. The ingest table is `parcels` (plural)
 * with columns id, apn, apn_norm, acres, geom. GEO-14 sets the final tile layer name;
 * keep this as a single constant so it is trivial to repoint.
 */
export const PARCELS_SOURCE_LAYER = "parcels";

/** Layer ids derived from the parcels source. */
export const PARCELS_FILL_LAYER = "parcels-fill";
export const PARCELS_LINE_LAYER = "parcels-line";
/**
 * Selected-parcel highlight is a hue-free DOUBLE CASING (design system §4.4): a wide white
 * outer line under a narrower near-black inner line. It collides with no categorical layer hue
 * (parcels indigo / transmission amber / substations magenta / flood cyan) and stays legible on
 * a light basemap, a dark basemap, and satellite imagery alike. Rendered as two stacked line
 * layers (outer drawn first, inner on top). Never the azure UI accent — that's reserved for chrome.
 */
export const PARCELS_HIGHLIGHT_OUTER_LAYER = "parcels-highlight-outer";
export const PARCELS_HIGHLIGHT_LAYER = "parcels-highlight";

/** Highlight casing colors + widths (px) for the selected parcel. */
export const HIGHLIGHT_OUTER_COLOR = "#ffffff";
export const HIGHLIGHT_INNER_COLOR = "#111827";
export const HIGHLIGHT_OUTER_WIDTH = 3;
export const HIGHLIGHT_INNER_WIDTH = 1.5;

/** Parcel fill/line color (design system §4.3) — indigo, distinct from the azure UI accent. */
export const PARCELS_COLOR = "#3b5bdb";

/* ── Static overlay layers (GeoJSON from /api/layer/<name>) — design system §4.3 ─────────────
 * Transmission lines, substations, and flood (SFHA) are served whole as GeoJSON and rendered as
 * MapLibre geojson sources. Their visibility/opacity are driven by the LAYERS registry (layers.ts)
 * via applyLayerState, which references the layer ids below.
 */
export const TRANSMISSION_SOURCE_ID = "transmission";
export const TRANSMISSION_CASING_LAYER = "transmission-casing";
export const TRANSMISSION_LINE_LAYER = "transmission-line";

export const SUBSTATIONS_SOURCE_ID = "substations";
export const SUBSTATIONS_CIRCLE_LAYER = "substations-circle";

export const FLOOD_SOURCE_ID = "flood";
export const FLOOD_FILL_LAYER = "flood-fill";
export const FLOOD_LINE_LAYER = "flood-line";
