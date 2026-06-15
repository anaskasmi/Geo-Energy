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
