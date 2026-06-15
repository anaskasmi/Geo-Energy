import { useEffect, useMemo, useRef } from "react";
import maplibregl from "maplibre-gl";
import { GeoJsonLayer } from "@deck.gl/layers";
import { MapboxOverlay } from "@deck.gl/mapbox";
import type { Feature } from "geojson";

import type { ScoredFeature, ScoredFeatureProps } from "../api/client";
import { applyBasemap, buildBaseStyle } from "../theme/basemap";
import { useTheme } from "../theme/useTheme";
import { MAP_CENTER, MAP_ZOOM, PARCELS_FILL_LAYER } from "./constants";
import { DrawController } from "./DrawController";
import { applyLayerState, RESULT_LAYER_ID, scoreColor } from "./layers";
import type { ParcelInfo } from "./MapContext";
import { addParcelsLayer, registerPmtilesProtocol, setSelectedParcel } from "./pmtiles";
import { useMapStore } from "./useMapStore";

/** Selected-parcel outline color in the deck overlay (matches HIGHLIGHT_COLOR #f97316). */
const HIGHLIGHT_RGB: [number, number, number] = [249, 115, 22];
const SCORED_LAYER_ID = "scored-parcels";

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

function parcelFromScored(feature: ScoredFeature): ParcelInfo {
  const p = feature.properties;
  return { id: p.id, apn: p.apn ?? null, acres: p.acres ?? null };
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
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const firstStyleSwap = useRef(true);

  const { resolvedTheme } = useTheme();
  const store = useMapStore();
  const {
    basemap,
    layers,
    selected,
    drawMode,
    scoreResult,
    setSelected,
    setDrawAreaSqm,
    setDrawHistory,
    setCanDeleteSelection,
    setDrawnPolygon,
    registerDrawApi,
    registerFlyTo,
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

    // Dev-only handle for E2E/manual debugging (stripped from production builds by Vite's
    // import.meta.env.DEV dead-code elimination). Never present in the shipped bundle.
    if (import.meta.env.DEV) {
      (window as unknown as { __map?: maplibregl.Map }).__map = map;
    }

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

    // Fly/zoom to a parcel when the results list requests it (GEO-25). Respects reduced-motion:
    // an instant jump instead of an animated flight when the user prefers reduced motion.
    registerFlyTo((lngLat, zoom) => {
      const target = { center: lngLat, zoom: zoom ?? Math.max(map.getZoom(), 12) };
      const reduce =
        typeof window !== "undefined" &&
        window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      if (reduce) map.jumpTo(target);
      else map.flyTo({ ...target, duration: 800, essential: true });
    });

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
          onGeometry: setDrawnPolygon,
        });
        registerDrawApi({
          undo: () => drawRef.current?.undo(),
          redo: () => drawRef.current?.redo(),
          clear: () => drawRef.current?.clearAll(),
          deleteSelected: () => drawRef.current?.deleteSelected(),
        });
        drawRef.current.setMode(drawModeRef.current);
      }
      // deck.gl overlay for scored parcels (GEO-24), overlaid above the MapLibre canvas so it
      // never disturbs the parcels/draw layers. Created once; updated via setProps below.
      if (!overlayRef.current) {
        overlayRef.current = new MapboxOverlay({ interleaved: false, layers: [] });
        map.addControl(overlayRef.current as unknown as maplibregl.IControl);
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
      registerFlyTo(null);
      if (overlayRef.current) {
        map.removeControl(overlayRef.current as unknown as maplibregl.IControl);
        overlayRef.current = null;
      }
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

  // Stable scored-features array (new reference ONLY when scoreResult changes), so deck.gl can
  // honor updateTriggers and recolor the selection outline without re-tessellating every fill.
  const scoredData = useMemo(
    () => (scoreResult?.features ?? []).filter((f) => f.geometry) as unknown as Feature[],
    [scoreResult],
  );
  // The result layer toggle object keeps its reference unless the RESULT toggle itself changes
  // (MapProvider replaces only the toggled entry), so an unrelated layer/opacity change here
  // does not re-run this effect.
  const resultToggle = layers[RESULT_LAYER_ID];

  // Render scored parcels on the deck.gl overlay, colored by score and outlined when selected
  // (GEO-24). Updated via setProps (no layer re-add); a stable data reference means selection
  // recolors the outline only. An empty/absent result clears the overlay.
  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    const toggle = resultToggle;
    const selectedId = selected?.id ?? null;
    const data = scoredData;
    const props = (f: Feature) => f.properties as unknown as ScoredFeatureProps;
    const layer =
      data.length > 0
        ? new GeoJsonLayer({
            id: SCORED_LAYER_ID,
            data,
            pickable: true,
            stroked: true,
            filled: true,
            visible: toggle?.visible ?? true,
            opacity: toggle?.opacity ?? 0.85,
            getFillColor: (f: Feature) => scoreColor(props(f).score, 220),
            getLineColor: (f: Feature) =>
              f.id === selectedId ? [...HIGHLIGHT_RGB, 255] : [255, 255, 255, 150],
            getLineWidth: (f: Feature) => (f.id === selectedId ? 3 : 1),
            lineWidthUnits: "pixels",
            lineWidthMinPixels: 1,
            onClick: (info) => {
              if (info.object) setSelected(parcelFromScored(info.object as ScoredFeature));
            },
            updateTriggers: {
              getLineColor: [selectedId],
              getLineWidth: [selectedId],
            },
          })
        : null;
    overlay.setProps({ layers: layer ? [layer] : [] });
  }, [scoredData, resultToggle, selected, setSelected]);

  return <div ref={containerRef} className="map-view" aria-label="Map" role="region" />;
}
