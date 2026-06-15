import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";

import { buildBaseStyle } from "../theme/basemap";
import { useTheme } from "../theme/useTheme";
import { MAP_CENTER, MAP_ZOOM } from "./constants";
import { addParcelsLayer, registerPmtilesProtocol } from "./pmtiles";

/**
 * Renders the MapLibre GL map:
 * - registers the `pmtiles://` protocol (byte-range vector tiles)
 * - renders a data-muted basemap that swaps with the app theme
 * - wires the parcels PMTiles source/layer (degrades gracefully if tiles are absent)
 * - keeps the canvas sized to its pane via a ResizeObserver
 */
export function MapView() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const firstThemeRun = useRef(true);
  const { resolvedTheme } = useTheme();

  // Initialize the map exactly once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current) return;

    registerPmtilesProtocol();

    const map = new maplibregl.Map({
      container,
      style: buildBaseStyle(resolvedTheme),
      center: MAP_CENTER,
      zoom: MAP_ZOOM,
      attributionControl: { compact: true },
    });
    mapRef.current = map;

    map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");

    // Re-add parcels whenever a (new) style finishes loading — incl. after theme swaps.
    map.on("style.load", () => addParcelsLayer(map));

    // Swallow source/tile errors (e.g. a missing parcels .pmtiles) so the basemap stays.
    map.on("error", (event) => {
      console.warn("[map] error:", event.error?.message ?? event.error ?? event);
    });

    // Keep the map sized to its container as the responsive layout changes.
    const resizeObserver = new ResizeObserver(() => map.resize());
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      map.remove();
      mapRef.current = null;
      firstThemeRun.current = true;
    };
    // resolvedTheme is intentionally excluded: initial style uses the current theme, and
    // subsequent theme changes are handled by the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Swap the basemap style when the theme changes (skip the initial render).
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (firstThemeRun.current) {
      firstThemeRun.current = false;
      return;
    }
    // setStyle wipes custom sources/layers; the "style.load" handler re-adds parcels.
    map.setStyle(buildBaseStyle(resolvedTheme));
  }, [resolvedTheme]);

  return <div ref={containerRef} className="map-view" aria-label="Map" role="region" />;
}
