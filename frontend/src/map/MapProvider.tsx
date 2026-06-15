import { useCallback, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import type { GeoJsonGeometry, ScoreFeatureCollection, UseCase } from "../api/client";
import type { ErrorAction } from "../api/errors";
import type { BasemapId } from "../theme/basemap";
import { MapContext } from "./MapContext";
import type { DrawApi, DrawMode, MapStore, ParcelInfo, ScoreStatus } from "./MapContext";
import { initialLayerState } from "./layers";
import type { LayerStateMap } from "./layers";

/** Provides shared map state (basemap, layer toggles, selection, drawing) to the SPA. */
export function MapProvider({ children }: { children: ReactNode }) {
  const [basemap, setBasemap] = useState<BasemapId>("auto");
  const [layers, setLayers] = useState<LayerStateMap>(initialLayerState);
  const [selected, setSelected] = useState<ParcelInfo | null>(null);
  const [drawMode, setDrawMode] = useState<DrawMode>("idle");
  const [drawAreaSqm, setDrawAreaSqm] = useState<number | null>(null);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);
  const [canDeleteSelection, setCanDeleteSelection] = useState(false);
  const drawApi = useRef<DrawApi | null>(null);

  const [useCase, setUseCase] = useState<UseCase>("utility_solar");
  const [drawnPolygon, setDrawnPolygon] = useState<GeoJsonGeometry | null>(null);
  const [weights, setWeights] = useState<Record<string, number> | null>(null);
  const [scoreResult, setScoreResult] = useState<ScoreFeatureCollection | null>(null);
  const [scoreStatus, setScoreStatusState] = useState<ScoreStatus>("idle");
  const [scoreError, setScoreError] = useState<string | null>(null);
  const [scoreErrorAction, setScoreErrorAction] = useState<ErrorAction | null>(null);
  const [scoreNonce, setScoreNonce] = useState(0);
  const [layerError, setLayerError] = useState(false);
  const flyToApi = useRef<((lngLat: [number, number], zoom?: number) => void) | null>(null);
  const snapshotApi = useRef<(() => string | null) | null>(null);

  const setLayerVisible = useCallback((id: string, visible: boolean) => {
    setLayers((prev) => ({ ...prev, [id]: { ...prev[id], visible } }));
  }, []);
  const setLayerOpacity = useCallback((id: string, opacity: number) => {
    setLayers((prev) => ({ ...prev, [id]: { ...prev[id], opacity } }));
  }, []);
  const setDrawHistory = useCallback((nextUndo: boolean, nextRedo: boolean) => {
    setCanUndo(nextUndo);
    setCanRedo(nextRedo);
  }, []);
  const registerDrawApi = useCallback((api: DrawApi | null) => {
    drawApi.current = api;
  }, []);
  const undo = useCallback(() => drawApi.current?.undo(), []);
  const redo = useCallback(() => drawApi.current?.redo(), []);
  const clearDraw = useCallback(() => drawApi.current?.clear(), []);
  const deleteSelection = useCallback(() => drawApi.current?.deleteSelected(), []);
  const setScoreStatus = useCallback(
    (status: ScoreStatus, error: string | null = null, action: ErrorAction | null = null) => {
      setScoreStatusState(status);
      setScoreError(error);
      setScoreErrorAction(action);
    },
    [],
  );
  const retryScore = useCallback(() => setScoreNonce((n) => n + 1), []);
  const registerFlyTo = useCallback(
    (fly: ((lngLat: [number, number], zoom?: number) => void) | null) => {
      flyToApi.current = fly;
    },
    [],
  );
  const flyTo = useCallback((lngLat: [number, number], zoom?: number) => {
    flyToApi.current?.(lngLat, zoom);
  }, []);
  const registerMapSnapshot = useCallback((snapshot: (() => string | null) | null) => {
    snapshotApi.current = snapshot;
  }, []);
  const captureMapSnapshot = useCallback(() => snapshotApi.current?.() ?? null, []);

  const value = useMemo<MapStore>(
    () => ({
      basemap,
      setBasemap,
      layers,
      setLayerVisible,
      setLayerOpacity,
      selected,
      setSelected,
      drawMode,
      setDrawMode,
      drawAreaSqm,
      setDrawAreaSqm,
      canUndo,
      canRedo,
      setDrawHistory,
      canDeleteSelection,
      setCanDeleteSelection,
      registerDrawApi,
      undo,
      redo,
      clearDraw,
      deleteSelection,
      useCase,
      setUseCase,
      drawnPolygon,
      setDrawnPolygon,
      weights,
      setWeights,
      scoreResult,
      setScoreResult,
      scoreStatus,
      scoreError,
      scoreErrorAction,
      setScoreStatus,
      scoreNonce,
      retryScore,
      registerFlyTo,
      flyTo,
      registerMapSnapshot,
      captureMapSnapshot,
      layerError,
      setLayerError,
    }),
    [
      basemap,
      layers,
      selected,
      drawMode,
      drawAreaSqm,
      canUndo,
      canRedo,
      canDeleteSelection,
      setLayerVisible,
      setLayerOpacity,
      setDrawHistory,
      registerDrawApi,
      undo,
      redo,
      clearDraw,
      deleteSelection,
      useCase,
      drawnPolygon,
      weights,
      scoreResult,
      scoreStatus,
      scoreError,
      scoreErrorAction,
      setScoreStatus,
      scoreNonce,
      retryScore,
      registerFlyTo,
      flyTo,
      registerMapSnapshot,
      captureMapSnapshot,
      layerError,
    ],
  );

  return <MapContext.Provider value={value}>{children}</MapContext.Provider>;
}
