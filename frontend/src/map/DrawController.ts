import maplibregl from "maplibre-gl";
import { area } from "@turf/area";
import {
  TerraDraw,
  TerraDrawModeUndoRedo,
  TerraDrawPolygonMode,
  TerraDrawSelectMode,
  TerraDrawSessionUndoRedo,
  TerraDrawUndoRedoKeyboardShortcuts,
} from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import type { GeoJSONStoreFeatures } from "terra-draw";

import type { DrawMode } from "./MapContext";

/** A GeoJSON geometry surfaced to React for scoring (Polygon, or MultiPolygon if several drawn). */
export interface DrawnGeometry {
  type: "Polygon" | "MultiPolygon";
  coordinates: unknown;
}

/** Callbacks back into React state when the drawing changes. */
export interface DrawControllerCallbacks {
  onArea: (sqm: number | null) => void;
  onHistory: (canUndo: boolean, canRedo: boolean) => void;
  onSelection: (hasSelection: boolean) => void;
  /** The current drawn area as a single geometry (null when nothing is drawn). GEO-24. */
  onGeometry: (geom: DrawnGeometry | null) => void;
}

type FeatureId = string | number;

// "static" is terra-draw's built-in non-interactive mode; polygon draws, select edits.
const MODE_NAME: Record<DrawMode, string> = {
  idle: "static",
  draw: "polygon",
  edit: "select",
};

/**
 * Wraps Terra Draw (GEO-23): polygon draw + select/edit (vertex drag, midpoint insert,
 * delete), built-in undo/redo + keyboard shortcuts, Escape-to-cancel (terra-draw default),
 * and a live turf area readout. Because a basemap/theme `setStyle` wipes terra-draw's layers,
 * `reattach()` rebuilds the instance on the new style while preserving the drawn features.
 */
export class DrawController {
  private readonly map: maplibregl.Map;
  private readonly cb: DrawControllerCallbacks;
  private draw: TerraDraw;
  private selectedId: FeatureId | null = null;

  constructor(map: maplibregl.Map, cb: DrawControllerCallbacks) {
    this.map = map;
    this.cb = cb;
    this.draw = this.create();
    this.draw.start();
    this.wire();
    // Fresh instance → empty undo/redo history + nothing selected. Reset the UI state so a
    // recreate (after a basemap/theme swap) can't leave stale enabled buttons.
    this.cb.onHistory(false, false);
    this.cb.onSelection(false);
    this.cb.onGeometry(null);
  }

  private create(): TerraDraw {
    // GEO-29: enlarge the draw/edit vertices on touch (coarse-pointer) devices so they meet
    // finger-sized hit targets; leave terra-draw's defaults on mouse/desktop.
    const coarse = typeof window !== "undefined" && !!window.matchMedia?.("(pointer: coarse)").matches;
    const polygonMode = coarse
      ? new TerraDrawPolygonMode({ styles: { closingPointWidth: 10, coordinatePointWidth: 8 } })
      : new TerraDrawPolygonMode();
    const selectFlags = {
      flags: {
        polygon: {
          feature: {
            draggable: true,
            coordinates: { midpoints: true, draggable: true, deletable: true },
          },
        },
      },
    };
    const selectMode = coarse
      ? new TerraDrawSelectMode({
          ...selectFlags,
          styles: { selectionPointWidth: 10, midPointWidth: 8 },
        })
      : new TerraDrawSelectMode(selectFlags);
    return new TerraDraw({
      adapter: new TerraDrawMapLibreGLAdapter({ map: this.map }),
      modes: [polygonMode, selectMode],
      undoRedo: {
        // modeLevel: undo/redo vertices WHILE drawing a polygon ("undo last vertex").
        // sessionLevel: undo/redo whole finished features + edits across the session.
        modeLevel: new TerraDrawModeUndoRedo(),
        sessionLevel: new TerraDrawSessionUndoRedo(),
        keyboardShortcuts: new TerraDrawUndoRedoKeyboardShortcuts(),
      },
    });
  }

  private wire(): void {
    this.draw.on("change", () => this.emitArea());
    this.draw.on("finish", () => this.emitArea());
    this.draw.on("history", (event) => this.cb.onHistory(event.undoSize > 0, event.redoSize > 0));
    this.draw.on("select", (id) => {
      this.selectedId = id;
      this.cb.onSelection(true);
    });
    this.draw.on("deselect", () => {
      this.selectedId = null;
      this.cb.onSelection(false);
    });
  }

  /** Delete the currently-selected feature (the GEO-23 "delete polygon"). The select mode
   * also deletes the selection on the Delete key (terra-draw default); this is the toolbar
   * path. No-op when nothing is selected. */
  deleteSelected(): void {
    if (this.selectedId == null) return;
    this.draw.removeFeatures([this.selectedId]);
    this.selectedId = null;
    this.cb.onSelection(false);
    this.emitArea();
  }

  private polygons(): GeoJSONStoreFeatures[] {
    return this.draw.getSnapshot().filter((f) => f.geometry?.type === "Polygon");
  }

  private emitArea(): void {
    let total = 0;
    for (const feature of this.polygons()) {
      total += area(feature as never);
    }
    this.cb.onArea(total > 0 ? total : null);
    this.emitGeometry();
  }

  /** Surface the drawn area to React: one Polygon, or a MultiPolygon when several are drawn. */
  private emitGeometry(): void {
    const polys = this.polygons();
    if (polys.length === 0) {
      this.cb.onGeometry(null);
      return;
    }
    if (polys.length === 1) {
      const g = polys[0].geometry as { type: "Polygon"; coordinates: unknown };
      this.cb.onGeometry({ type: "Polygon", coordinates: g.coordinates });
      return;
    }
    this.cb.onGeometry({
      type: "MultiPolygon",
      coordinates: polys.map((p) => (p.geometry as { coordinates: unknown }).coordinates),
    });
  }

  setMode(mode: DrawMode): void {
    this.draw.setMode(MODE_NAME[mode]);
  }

  undo(): void {
    this.draw.undo();
    this.emitArea();
  }

  redo(): void {
    this.draw.redo();
    this.emitArea();
  }

  clearAll(): void {
    this.draw.clear();
    this.selectedId = null;
    this.cb.onArea(null);
    this.cb.onHistory(false, false);
    this.cb.onSelection(false);
    this.cb.onGeometry(null);
  }

  destroy(): void {
    try {
      this.draw.stop();
    } catch {
      // already torn down
    }
  }
}
