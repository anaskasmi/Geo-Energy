import type { StyleSpecification } from "maplibre-gl";

import { HAS_MAPBOX_TOKEN, MAPBOX_TOKEN } from "../config/env";
import type { ResolvedTheme } from "./theme";

/**
 * Builds a MapLibre basemap style for the given theme.
 *
 * The basemap is intentionally "data-muted" (low-contrast, desaturated) so that data
 * overlays (parcels now; scores via deck.gl in GEO-24) stand out. It swaps with the app
 * theme: a light basemap for light mode, a dark basemap for dark mode.
 *
 * - If a Mapbox token is configured (VITE_MAPBOX_TOKEN), Mapbox's Light/Dark raster
 *   tiles are used.
 * - Otherwise we fall back to CARTO's free, token-less Positron / Dark Matter basemaps
 *   (also data-muted), so the map always renders without any credentials.
 *
 * A background layer underneath guarantees the map shows a sensible color even if the
 * raster tiles fail to load (graceful degradation).
 */

const CARTO_TILES: Record<ResolvedTheme, string> = {
  light: "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
  dark: "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
};

const BACKGROUND_COLOR: Record<ResolvedTheme, string> = {
  light: "#e9e6e1",
  dark: "#16181d",
};

const ATTRIBUTION: Record<ResolvedTheme, string> = {
  light: HAS_MAPBOX_TOKEN
    ? '&copy; <a href="https://www.mapbox.com/about/maps/">Mapbox</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    : '&copy; <a href="https://carto.com/attributions">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  dark: HAS_MAPBOX_TOKEN
    ? '&copy; <a href="https://www.mapbox.com/about/maps/">Mapbox</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    : '&copy; <a href="https://carto.com/attributions">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
};

function basemapTileUrl(theme: ResolvedTheme): string {
  if (HAS_MAPBOX_TOKEN) {
    const styleId = theme === "dark" ? "dark-v11" : "light-v11";
    return `https://api.mapbox.com/styles/v1/mapbox/${styleId}/tiles/256/{z}/{x}/{y}?access_token=${MAPBOX_TOKEN}`;
  }
  return CARTO_TILES[theme];
}

export function buildBaseStyle(theme: ResolvedTheme): StyleSpecification {
  return {
    version: 8,
    // Glyphs let us add text labels to overlays later without another style edit.
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
    sources: {
      basemap: {
        type: "raster",
        tiles: [basemapTileUrl(theme)],
        tileSize: 256,
        attribution: ATTRIBUTION[theme],
      },
    },
    layers: [
      {
        id: "background",
        type: "background",
        paint: { "background-color": BACKGROUND_COLOR[theme] },
      },
      {
        id: "basemap",
        type: "raster",
        source: "basemap",
        // Slightly fade the basemap so data overlays read clearly.
        paint: { "raster-opacity": 0.9 },
      },
    ],
  };
}
