import type { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";

import { HAS_MAPBOX_TOKEN, MAPBOX_TOKEN } from "../config/env";
import type { ResolvedTheme } from "./theme";

const BASEMAP_SOURCE_ID = "basemap";
const BASEMAP_LAYER_ID = "basemap";
const BACKGROUND_LAYER_ID = "background";

/**
 * Builds a MapLibre basemap style for the chosen basemap + app theme (GEO-22 + GEO-26).
 *
 * The basemap is intentionally "data-muted" (faded raster) so data overlays (parcels now;
 * scores via deck.gl in GEO-24) read clearly. The user can pick a basemap explicitly
 * (light / dark / streets / satellite) or leave it on "auto", which follows the app theme
 * (light basemap in light mode, dark in dark mode).
 *
 * All sources are token-less so the map always renders without credentials:
 * - light/dark → CARTO Positron / Dark Matter (or Mapbox Light/Dark if VITE_MAPBOX_TOKEN set)
 * - streets    → Esri World Street Map
 * - satellite  → Esri World Imagery
 *
 * A background layer underneath guarantees a sensible color even if tiles fail to load.
 */

/** What the basemap control offers. "auto" tracks the app theme. */
export type BasemapId = "auto" | "light" | "dark" | "streets" | "satellite";

/** A basemap with an explicit raster source (auto resolves to light/dark first). */
type ConcreteBasemap = "light" | "dark" | "streets" | "satellite";

const CARTO_TILES: Record<ResolvedTheme, string> = {
  light: "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
  dark: "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
};

// Esri ArcGIS Online tiled basemaps — token-free, global CDN (reachable everywhere).
const ESRI_IMAGERY =
  "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const ESRI_STREETS =
  "https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}";

// Attribution intentionally blank for the Esri basemaps (product decision): the on-map
// "Tiles © Esri — …, Maxar, Earthstar Geographics, and the GIS community" credit is suppressed.
const ESRI_ATTRIB = "";
const CARTO_ATTRIB =
  '&copy; <a href="https://carto.com/attributions">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
const MAPBOX_ATTRIB =
  '&copy; <a href="https://www.mapbox.com/about/maps/">Mapbox</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

// Cool-neutral fallback under the (data-muted) basemap raster. Design system §4.1: drop the
// warm tan/sepia in light and match Dark Matter's near-black in dark so the basemap reads as
// neutral data-ink, never tinted.
const BACKGROUND_COLOR: Record<ResolvedTheme, string> = {
  light: "#f2f2f0",
  dark: "#0e0e0e",
};

interface RasterSource {
  tiles: string;
  attribution: string;
  /** Faded so overlays read clearly; satellite stays a touch crisper. */
  opacity: number;
  /** Background under the raster (matches theme so gaps aren't jarring). */
  background: string;
}

function lightDarkSource(theme: ResolvedTheme): RasterSource {
  // CARTO Positron / Dark Matter (and Mapbox Light/Dark) are already data-muted, so render them
  // crisp at full opacity (§4.1) — the previous 0.9 double-faded an already-faded basemap.
  if (HAS_MAPBOX_TOKEN) {
    const styleId = theme === "dark" ? "dark-v11" : "light-v11";
    return {
      tiles: `https://api.mapbox.com/styles/v1/mapbox/${styleId}/tiles/256/{z}/{x}/{y}?access_token=${MAPBOX_TOKEN}`,
      attribution: MAPBOX_ATTRIB,
      opacity: 1,
      background: BACKGROUND_COLOR[theme],
    };
  }
  return {
    tiles: CARTO_TILES[theme],
    attribution: CARTO_ATTRIB,
    opacity: 1,
    background: BACKGROUND_COLOR[theme],
  };
}

function resolveSource(basemap: ConcreteBasemap, theme: ResolvedTheme): RasterSource {
  switch (basemap) {
    case "streets":
      return { tiles: ESRI_STREETS, attribution: ESRI_ATTRIB, opacity: 0.9, background: BACKGROUND_COLOR[theme] };
    case "satellite":
      return { tiles: ESRI_IMAGERY, attribution: ESRI_ATTRIB, opacity: 0.96, background: "#0b0d10" };
    case "light":
      return lightDarkSource("light");
    case "dark":
      return lightDarkSource("dark");
  }
}

/** Resolve "auto" to the theme-driven light/dark basemap. */
function concreteBasemap(basemap: BasemapId, theme: ResolvedTheme): ConcreteBasemap {
  return basemap === "auto" ? theme : basemap;
}

export function buildBaseStyle(theme: ResolvedTheme, basemap: BasemapId = "auto"): StyleSpecification {
  const src = resolveSource(concreteBasemap(basemap, theme), theme);
  return {
    version: 8,
    // Glyphs let us add text labels to overlays later without another style edit.
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
    sources: {
      [BASEMAP_SOURCE_ID]: {
        type: "raster",
        tiles: [src.tiles],
        tileSize: 256,
        attribution: src.attribution,
      },
    },
    layers: [
      {
        id: BACKGROUND_LAYER_ID,
        type: "background",
        paint: { "background-color": src.background },
      },
      {
        id: BASEMAP_LAYER_ID,
        type: "raster",
        source: BASEMAP_SOURCE_ID,
        paint: { "raster-opacity": src.opacity },
      },
    ],
  };
}

/**
 * Swap the basemap + background IN PLACE on theme/basemap change, leaving every data layer
 * (parcels, selection highlight, the terra-draw drawing layers) untouched. This avoids a full
 * `map.setStyle()`, which would wipe those layers — and in particular would force the drawing
 * tool to be torn down and rebuilt, corrupting its undo history. The basemap raster source is
 * removed + re-added (so its attribution updates too) and re-inserted just above the
 * background, below all data layers.
 */
export function applyBasemap(map: MapLibreMap, basemap: BasemapId, theme: ResolvedTheme): void {
  // Gate on the basemap layer existing, NOT on map.isStyleLoaded(): the latter stays false
  // whenever ANY source is still pending (tiles loading mid-pan) or failing (e.g. a missing
  // parcels PMTiles), which would silently freeze the basemap selector. The basemap +
  // background layers are created at init (buildBaseStyle), so once they exist the style is
  // structurally ready to hot-swap and the getLayer/getSource/addLayer calls below are safe.
  if (!map.getLayer(BASEMAP_LAYER_ID)) return;
  const src = resolveSource(concreteBasemap(basemap, theme), theme);

  if (map.getLayer(BACKGROUND_LAYER_ID)) {
    map.setPaintProperty(BACKGROUND_LAYER_ID, "background-color", src.background);
  }
  if (map.getLayer(BASEMAP_LAYER_ID)) map.removeLayer(BASEMAP_LAYER_ID);
  if (map.getSource(BASEMAP_SOURCE_ID)) map.removeSource(BASEMAP_SOURCE_ID);

  map.addSource(BASEMAP_SOURCE_ID, {
    type: "raster",
    tiles: [src.tiles],
    tileSize: 256,
    attribution: src.attribution,
  });
  // Insert above the background but below the first data layer so overlays stay on top.
  const firstDataLayer = map
    .getStyle()
    .layers.find((l) => l.id !== BACKGROUND_LAYER_ID && l.id !== BASEMAP_LAYER_ID);
  map.addLayer(
    {
      id: BASEMAP_LAYER_ID,
      type: "raster",
      source: BASEMAP_SOURCE_ID,
      paint: { "raster-opacity": src.opacity },
    },
    firstDataLayer?.id,
  );
}
