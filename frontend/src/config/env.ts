/**
 * Typed wrapper around `import.meta.env` (Vite build-time env).
 *
 * Centralizes reads of the VITE_* vars so the rest of the app never touches
 * `import.meta.env` directly, and applies sensible defaults so the SPA renders even with
 * an empty .env. Env var names mirror the root .env.example / docker-compose (GEO-33).
 */

/** Base URL the SPA calls for the API. Default "/api" (relative, proxied by nginx). */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "/api";

/**
 * Mapbox access token for basemap tiles. When empty, the app uses a free, token-less
 * data-muted basemap (CARTO) so the map still renders — see theme/basemap.ts.
 */
export const MAPBOX_TOKEN: string = import.meta.env.VITE_MAPBOX_TOKEN ?? "";

/** Whether a Mapbox token is configured. */
export const HAS_MAPBOX_TOKEN: boolean = MAPBOX_TOKEN.trim().length > 0;

/**
 * URL of the parcels PMTiles archive (HTTP byte-range). GEO-14 produces the real file;
 * until then this may 404 and the map degrades gracefully to the basemap only.
 */
export const PARCELS_PMTILES_URL: string =
  import.meta.env.VITE_PARCELS_PMTILES_URL ?? "/data/current/parcels.pmtiles";
