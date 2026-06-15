import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";

import { applyBasemap, buildBaseStyle } from "../theme/basemap";
import { useTheme } from "../theme/useTheme";
import { MAP_CENTER, MAP_ZOOM, PARCELS_FILL_LAYER } from "./constants";
import { DrawController } from "./DrawController";
import { applyLayerState } from "./layers";
import type { ParcelInfo } from "./MapContext";
import { addParcelsLayer, registerPmtilesProtocol, setSelectedParcel } from "./pmtiles";
import { useMapStore } from "./useMapStore";

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function toAcres(value: unknown): number | null {
  const n = typeof value === "number" ? value : value != null ? Number(value) : NaN;
  return Number.isFinite(n) ? n : null;
}

function parcelFromFeature(props: Record<string, unknown> | null): ParcelInfo {
  const p = props ?? {};
  const id = (p.id as number | string | undefined) ?? (p.apn as string | undefined) ?? "?";
  return { id, apn: (p.apn as string | undefined) ?? null, acres: toAcres(p.acres) };
}

/**
 * Renders the MapLibre GL map and all map interaction (GEO-26 controls/layers + GEO-23
 * drawing). MapView is the single owner of the map instance: it applies basemap/theme,
 * layer toggle/opacity, selection highlight, and draw-mode changes by watching the shared
 * MapStore, and pushes hover/select + draw state back into it.
 */
export function MapView() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const drawRef = useRef<DrawController | null>(null);
  const firstStyleSwap = useRef(true);

  const { resolvedTheme } = useTheme();
  const store = useMapStore();
  const {
    basemap,
    layers,
    selected,
    drawMode,
    setSelected,
    setDrawAreaSqm,
    setDrawHistory,
    setCanDeleteSelection,
    registerDrawApi,
  } = store;

  // Refs so the once-registered map handlers read the latest state without re-binding.
  const layersRef = useRef(layers);
  layersRef.current = layers;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const drawModeRef = useRef(drawMode);
  drawModeRef.current = drawMode;
  const basemapRef = useRef(basemap);
  basemapRef.current = basemap;
  const themeRef = useRef(resolvedTheme);
  themeRef.current = resolvedTheme;

  // Initialize the map exactly once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container || mapRef.current) return;

    registerPmtilesProtocol();

    const map = new maplibregl.Map({
      container,
      style: buildBaseStyle(themeRef.current, basemapRef.current),
      center: MAP_CENTER,
      zoom: MAP_ZOOM,
      attributionControl: { compact: true },
    });
    mapRef.current = map;

    // Controls: zoom + compass/reset-north, imperial scale bar, geolocate.
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: false, showCompass: true }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");
    map.addControl(
      new maplibregl.GeolocateControl({
        positionOptions: { enableHighAccuracy: true },
        trackUserLocation: true,
      }),
      "top-right",
    );

    // Add parcels + the drawing tool once the style loads. The basemap/theme effect swaps the
    // basemap IN PLACE (no setStyle), so these data layers + the draw controller persist and
    // style.load fires only once — the drawing's undo history is never corrupted by a rebuild.
    map.on("style.load", () => {
      addParcelsLayer(map);
      applyLayerState(map, layersRef.current);
      setSelectedParcel(map, selectedRef.current?.id ?? null);
      if (!drawRef.current) {
        drawRef.current = new DrawController(map, {
          onArea: setDrawAreaSqm,
          onHistory: setDrawHistory,
          onSelection: setCanDeleteSelection,
        });
        registerDrawApi({
          undo: () => drawRef.current?.undo(),
          redo: () => drawRef.current?.redo(),
          clear: () => drawRef.current?.clearAll(),
          deleteSelected: () => drawRef.current?.deleteSelected(),
        });
        drawRef.current.setMode(drawModeRef.current);
      }
    });

    // Hover tooltip (desktop) + click-to-select (desktop & mobile tap).
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: "parcel-popup" });
    map.on("mousemove", PARCELS_FILL_LAYER, (event) => {
      map.getCanvas().style.cursor = "pointer";
      const feature = event.features?.[0];
      if (!feature) return;
      const info = parcelFromFeature(feature.properties as Record<string, unknown>);
      const acres = info.acres != null ? `${info.acres.toFixed(1)} ac` : "";
      popup
        .setLngLat(event.lngLat)
        .setHTML(`<strong>APN</strong> ${escapeHtml(info.apn ?? "—")} <span>${acres}</span>`)
        .addTo(map);
    });
    map.on("mouseleave", PARCELS_FILL_LAYER, () => {
      map.getCanvas().style.cursor = "";
      popup.remove();
    });
    map.on("click", PARCELS_FILL_LAYER, (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      setSelected(parcelFromFeature(feature.properties as Record<string, unknown>));
    });

    // Swallow source/tile errors (e.g. a missing parcels .pmtiles) so the basemap stays.
    map.on("error", (event) => {
      console.warn("[map] error:", event.error?.message ?? event.error ?? event);
    });

    const resizeObserver = new ResizeObserver(() => map.resize());
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      popup.remove();
      drawRef.current?.destroy();
      drawRef.current = null;
      registerDrawApi(null);
      map.remove();
      mapRef.current = null;
      firstStyleSwap.current = true;
    };
    // Map is created once; state is applied via the effects below + the style.load handler.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Swap the basemap + background in place when the basemap choice or theme changes (skip the
  // initial render — the map was created with the right basemap). In-place keeps parcels +
  // the drawing intact, so the drawing survives and its undo history is never reset.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (firstStyleSwap.current) {
      firstStyleSwap.current = false;
      return;
    }
    applyBasemap(map, basemap, resolvedTheme);
  }, [resolvedTheme, basemap]);

  // Apply layer visibility/opacity whenever the toggles change.
  useEffect(() => {
    const map = mapRef.current;
    if (map) applyLayerState(map, layers);
  }, [layers]);

  // Move the selection highlight when the selected parcel changes.
  useEffect(() => {
    const map = mapRef.current;
    if (map) setSelectedParcel(map, selected?.id ?? null);
  }, [selected]);

  // Switch the drawing tool mode.
  useEffect(() => {
    drawRef.current?.setMode(drawMode);
  }, [drawMode]);

  return <div ref={containerRef} className="map-view" aria-label="Map" role="region" />;
}
