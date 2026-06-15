import { createContext } from "react";

import type { GeoJsonGeometry, ScoreFeatureCollection, UseCase } from "../api/client";
import type { ErrorAction } from "../api/errors";
import type { BasemapId } from "../theme/basemap";
import type { LayerStateMap } from "./layers";

/** Quick attributes shown for a hovered/selected parcel (from the vector tile feature). */
export interface ParcelInfo {
  id: number | string;
  apn: string | null;
  acres: number | null;
}

/** Map interaction modes for the drawing tool (GEO-23). */
export type DrawMode = "idle" | "draw" | "edit";

/** Lifecycle of a /api/score request (GEO-24). */
export type ScoreStatus = "idle" | "scoring" | "done" | "error";

/** Imperative draw actions, wired by MapView (which owns the terra-draw controller). */
export interface DrawApi {
  undo(): void;
  redo(): void;
  clear(): void;
  deleteSelected(): void;
}

/**
 * Shared map state for the SPA. MapView owns the MapLibre instance and applies every map
 * mutation by watching this state; the control panels/toolbar only read state and call the
 * setters. Imperative draw actions are delegated through a registered DrawApi.
 */
export interface MapStore {
  basemap: BasemapId;
  setBasemap: (basemap: BasemapId) => void;

  layers: LayerStateMap;
  setLayerVisible: (id: string, visible: boolean) => void;
  setLayerOpacity: (id: string, opacity: number) => void;

  selected: ParcelInfo | null;
  setSelected: (parcel: ParcelInfo | null) => void;

  drawMode: DrawMode;
  setDrawMode: (mode: DrawMode) => void;
  drawAreaSqm: number | null;
  setDrawAreaSqm: (sqm: number | null) => void;
  canUndo: boolean;
  canRedo: boolean;
  setDrawHistory: (canUndo: boolean, canRedo: boolean) => void;
  canDeleteSelection: boolean;
  setCanDeleteSelection: (canDelete: boolean) => void;

  registerDrawApi: (api: DrawApi | null) => void;
  undo: () => void;
  redo: () => void;
  clearDraw: () => void;
  deleteSelection: () => void;

  // --- Scoring (GEO-24/25) ---------------------------------------------------
  /** The use case scored against (drives weights + prohibited zoning). */
  useCase: UseCase;
  setUseCase: (useCase: UseCase) => void;
  /** The drawn search area (Polygon/MultiPolygon, EPSG:4326), surfaced by the DrawController. */
  drawnPolygon: GeoJsonGeometry | null;
  setDrawnPolygon: (geom: GeoJsonGeometry | null) => void;
  /** Optional per-factor weights override (GEO-31); null → backend use-case defaults. */
  weights: Record<string, number> | null;
  setWeights: (weights: Record<string, number> | null) => void;
  /** Latest scored FeatureCollection from POST /api/score. */
  scoreResult: ScoreFeatureCollection | null;
  setScoreResult: (result: ScoreFeatureCollection | null) => void;
  scoreStatus: ScoreStatus;
  scoreError: string | null;
  /** Recovery affordance for the current scoring error (GEO-32 #10). */
  scoreErrorAction: ErrorAction | null;
  setScoreStatus: (status: ScoreStatus, error?: string | null, action?: ErrorAction | null) => void;
  /** Bumped to force a re-score of the current area (the Retry affordance; GEO-32 #11). */
  scoreNonce: number;
  retryScore: () => void;

  /** Fly/zoom the map to a [lng, lat] (registered by MapView; respects reduced-motion). */
  registerFlyTo: (fly: ((lngLat: [number, number], zoom?: number) => void) | null) => void;
  flyTo: (lngLat: [number, number], zoom?: number) => void;

  /** Capture a PNG data URL of the current map canvas for the PDF one-pager (GEO-31 #5). */
  registerMapSnapshot: (snapshot: (() => string | null) | null) => void;
  captureMapSnapshot: () => string | null;

  /** True when the parcels tile source/layer failed to load (GEO-32 #10/#12). */
  layerError: boolean;
  setLayerError: (failed: boolean) => void;
}

export const MapContext = createContext<MapStore | null>(null);
