/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL the SPA calls for the API. Default "/api". */
  readonly VITE_API_BASE_URL: string;
  /** Mapbox access token for basemap tiles. Empty -> token-less fallback basemap. */
  readonly VITE_MAPBOX_TOKEN: string;
  /** Optional URL to the parcels PMTiles archive (GEO-14 produces the real file). */
  readonly VITE_PARCELS_PMTILES_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
