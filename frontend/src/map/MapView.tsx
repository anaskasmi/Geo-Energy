import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { GeoJsonLayer } from "@deck.gl/layers";
import { MapboxOverlay } from "@deck.gl/mapbox";
import type { Feature } from "geojson";

import type { ScoredFeature, ScoredFeatureProps } from "../api/client";
import { applyBasemap, buildBaseStyle } from "../theme/basemap";
import { useTheme } from "../theme/useTheme";
import { MAP_CENTER, MAP_ZOOM, PARCELS_FILL_LAYER, PARCELS_SOURCE_ID } from "./constants";
import { DrawController } from "./DrawController";
import { applyLayerState, RESULT_LAYER_ID, scoreColor } from "./layers";
import type { ParcelInfo } from "./MapContext";
import { addParcelsLayer, registerPmtilesProtocol, setSelectedParcel } from "./pmtiles";
import { useMapStore } from "./useMapStore";
import { haptic } from "../utils/haptics";

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
  // Desktop right-click context menu (GEO-30): screen px + the clicked lng/lat.
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; lng: number; lat: number } | null>(null);

  const { resolvedTheme } = useTheme();
  const store = useMapStore();
  const {
    basemap,
    layers,
    selected,
    drawMode,
    scoreResult,
    drawnPolygon,
    layerError,
    setSelected,
    setDrawMode,
    setDrawAreaSqm,
    setDrawHistory,
    setCanDeleteSelection,
    setDrawnPolygon,
    setLayerError,
    registerDrawApi,
    registerFlyTo,
    registerMapSnapshot,
  } = store;
  const [layerNoticeDismissed, setLayerNoticeDismissed] = useState(false);

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
  // Latest drawn polygon, for the once-bound map handlers + the inject effect below.
  const drawnPolygonRef = useRef(drawnPolygon);
  drawnPolygonRef.current = drawnPolygon;
  // The exact geometry object terra-draw last emitted. The inject effect compares by reference to
  // tell a user-draw (already in terra-draw) from an EXTERNAL hydration (URL/saved/example) that
  // must be pushed INTO terra-draw — so it injects once and never echoes into a loop (GEO-31).
  const lastFromDrawRef = useRef<object | null>(null);

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
      // Keep the WebGL backbuffer so map.getCanvas().toDataURL() works for the PDF snapshot
      // (GEO-31 #5). Small memory cost; required for an off-frame canvas read.
      preserveDrawingBuffer: true,
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

    // Expose a production-safe map snapshot for the per-parcel PDF (GEO-31 #5). Best-effort:
    // returns null if the read fails (e.g. canvas tainted) rather than throwing.
    registerMapSnapshot(() => {
      try {
        return map.getCanvas().toDataURL("image/png");
      } catch {
        return null;
      }
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
          onGeometry: (g) => {
            // Remember terra-draw's own emission so the inject effect won't push it straight back.
            lastFromDrawRef.current = g;
            setDrawnPolygon(g);
          },
        });
        registerDrawApi({
          undo: () => drawRef.current?.undo(),
          redo: () => drawRef.current?.redo(),
          clear: () => drawRef.current?.clearAll(),
          deleteSelected: () => drawRef.current?.deleteSelected(),
        });
        drawRef.current.setMode(drawModeRef.current);
        // A geometry hydrated from the URL/saved/example BEFORE the draw tool existed: render it
        // now as an editable outline (the store already holds it as the scoring source of truth).
        if (drawnPolygonRef.current && drawnPolygonRef.current !== lastFromDrawRef.current) {
          drawRef.current.setGeometry(drawnPolygonRef.current as never);
        }
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
      if (drawModeRef.current !== "idle") return; // don't fight the draw crosshair / interrupt drawing
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
      map.getCanvas().style.cursor = drawModeRef.current === "draw" ? "crosshair" : "";
      popup.remove();
    });
    map.on("click", PARCELS_FILL_LAYER, (event) => {
      const feature = event.features?.[0];
      if (!feature) return;
      setSelected(parcelFromFeature(feature.properties as Record<string, unknown>));
    });

    // Right-click context menu (GEO-30): draw here / center / copy coords. A left click or a map
    // move dismisses it. `suppressNextClick` stops a touch long-press's trailing synthetic click
    // from immediately closing the menu it just opened (GEO-29).
    let suppressNextClick = false;
    map.on("contextmenu", (event) => {
      event.preventDefault();
      setCtxMenu({ x: event.point.x, y: event.point.y, lng: event.lngLat.lng, lat: event.lngLat.lat });
    });
    map.on("click", () => {
      if (suppressNextClick) {
        suppressNextClick = false;
        return;
      }
      setCtxMenu(null);
    });
    map.on("movestart", () => setCtxMenu(null));

    // Long-press (touch) opens the same context menu — mobile parity with desktop right-click
    // (GEO-29). A 500 ms hold without panning (<12 px move) fires; a short tap / pan does not.
    const canvas = map.getCanvas();
    let lpTimer: number | undefined;
    let lpStart: { x: number; y: number } | null = null;
    const clearLp = () => {
      if (lpTimer !== undefined) {
        window.clearTimeout(lpTimer);
        lpTimer = undefined;
      }
    };
    const onTouchStart = (ev: TouchEvent) => {
      clearLp();
      if (ev.touches.length !== 1) return;
      const t = ev.touches[0];
      lpStart = { x: t.clientX, y: t.clientY };
      const rect = canvas.getBoundingClientRect();
      const px = t.clientX - rect.left;
      const py = t.clientY - rect.top;
      lpTimer = window.setTimeout(() => {
        const ll = map.unproject([px, py]);
        haptic(15);
        suppressNextClick = true; // don't let the trailing click close the menu we just opened
        setCtxMenu({ x: px, y: py, lng: ll.lng, lat: ll.lat });
      }, 500);
    };
    const onTouchMove = (ev: TouchEvent) => {
      if (!lpStart || ev.touches.length === 0) {
        clearLp();
        return;
      }
      const t = ev.touches[0];
      if (Math.hypot(t.clientX - lpStart.x, t.clientY - lpStart.y) > 12) clearLp();
    };
    canvas.addEventListener("touchstart", onTouchStart, { passive: true });
    canvas.addEventListener("touchmove", onTouchMove, { passive: true });
    canvas.addEventListener("touchend", clearLp, { passive: true });
    canvas.addEventListener("touchcancel", clearLp, { passive: true });
    // A pan/drag cancels a pending long-press (so it can't fire at a now-stale coordinate).
    map.on("dragstart", clearLp);

    // Swallow source/tile errors (e.g. a missing parcels .pmtiles) so the basemap stays, and
    // surface a single user-facing notice when the parcels data layer fails (GEO-32 #10/#12).
    map.on("error", (event) => {
      console.warn("[map] error:", event.error?.message ?? event.error ?? event);
      const sourceId = (event as unknown as { sourceId?: string }).sourceId;
      if (sourceId === PARCELS_SOURCE_ID) setLayerError(true);
    });
    // Clear the notice once the parcels source loads successfully, so a transient tile/network
    // blip doesn't permanently latch a false "unavailable" message (GEO-32 #10/#12).
    map.on("sourcedata", (event) => {
      const e = event as unknown as { sourceId?: string; isSourceLoaded?: boolean };
      if (e.sourceId === PARCELS_SOURCE_ID && e.isSourceLoaded) setLayerError(false);
    });

    const resizeObserver = new ResizeObserver(() => map.resize());
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      clearLp();
      canvas.removeEventListener("touchstart", onTouchStart);
      canvas.removeEventListener("touchmove", onTouchMove);
      canvas.removeEventListener("touchend", clearLp);
      canvas.removeEventListener("touchcancel", clearLp);
      popup.remove();
      drawRef.current?.destroy();
      drawRef.current = null;
      registerDrawApi(null);
      registerFlyTo(null);
      registerMapSnapshot(null);
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

  // Switch the drawing tool mode + show a crosshair cursor while drawing (GEO-30).
  useEffect(() => {
    drawRef.current?.setMode(drawMode);
    const map = mapRef.current;
    if (map) map.getCanvas().style.cursor = drawMode === "draw" ? "crosshair" : "";
  }, [drawMode]);

  // Render an EXTERNAL geometry (URL/saved/example hydration) into terra-draw so the shared search
  // area is visible + editable (GEO-31). Reference-compared against terra-draw's own last emission
  // so a user-draw isn't re-injected (no echo loop); setGeometry doesn't fire onGeometry. The
  // controller may not exist yet on first hydration — the creation block handles that case.
  useEffect(() => {
    if (drawnPolygon !== lastFromDrawRef.current) {
      drawRef.current?.setGeometry((drawnPolygon ?? null) as never);
    }
  }, [drawnPolygon]);

  // Dismiss the context menu on Escape or a click/tap outside it (GEO-30).
  useEffect(() => {
    if (!ctxMenu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setCtxMenu(null);
    };
    const onDown = (e: Event) => {
      const menu = document.querySelector(".map-context-menu");
      if (!menu || !menu.contains(e.target as Node)) setCtxMenu(null);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown, true);
    };
  }, [ctxMenu]);

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

  return (
    <>
      <div ref={containerRef} className="map-view" aria-label="Map" role="region" />
      {layerError && !layerNoticeDismissed && (
        <div className="map-notice" role="status">
          <span>
            <strong>Map data layer is unavailable.</strong> Parcel outlines couldn&apos;t load — the
            basemap and scoring still work.
          </span>
          <button
            type="button"
            className="map-notice__close"
            aria-label="Dismiss"
            onClick={() => setLayerNoticeDismissed(true)}
          >
            ✕
          </button>
        </div>
      )}
      {ctxMenu && (
        <ul className="map-context-menu" style={{ left: ctxMenu.x, top: ctxMenu.y }} role="menu" aria-label="Map actions">
          <li role="none">
            <button
              role="menuitem"
              type="button"
              onClick={() => {
                setDrawMode("draw");
                setCtxMenu(null);
              }}
            >
              Draw here
            </button>
          </li>
          <li role="none">
            <button
              role="menuitem"
              type="button"
              onClick={() => {
                const map = mapRef.current;
                if (map) {
                  const target = { center: [ctxMenu.lng, ctxMenu.lat] as [number, number] };
                  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
                  if (reduce) map.jumpTo(target);
                  else map.flyTo({ ...target, duration: 600, essential: true });
                }
                setCtxMenu(null);
              }}
            >
              Center here
            </button>
          </li>
          <li role="none">
            <button
              role="menuitem"
              type="button"
              onClick={() => {
                void navigator.clipboard
                  ?.writeText(`${ctxMenu.lat.toFixed(6)}, ${ctxMenu.lng.toFixed(6)}`)
                  .catch(() => {});
                setCtxMenu(null);
              }}
            >
              Copy coordinates
            </button>
          </li>
        </ul>
      )}
    </>
  );
}
