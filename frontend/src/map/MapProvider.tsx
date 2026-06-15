import { useCallback, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import type { BasemapId } from "../theme/basemap";
import { MapContext } from "./MapContext";
import type { DrawApi, DrawMode, MapStore, ParcelInfo } from "./MapContext";
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
    ],
  );

  return <MapContext.Provider value={value}>{children}</MapContext.Provider>;
}
