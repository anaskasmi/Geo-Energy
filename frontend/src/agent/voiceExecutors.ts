import { apiClient } from "../api/client";
import type { ScoreFeatureCollection, UseCase } from "../api/client";
import { generateParcelsReport } from "../export/parcelReport";
import type { ParcelInfo, ScoreStatus } from "../map/MapContext";
import { LAYERS, resolveLayerNames } from "../map/layers";
import type { LayerStateMap } from "../map/layers";
import { PLACE_LABELS, resolvePlace } from "../map/places";
import { ZOOM_PERCENT_DEFAULT } from "../map/zoom";
import type { ZoomDirection } from "../map/zoom";
import { resolveBasemapMode } from "../theme/basemap";
import type { BasemapId } from "../theme/basemap";

/**
 * Voice-tool EXECUTORS (GEO-42), split out of AgentPanel so the voice surface is an enforced mirror
 * of the shared registry (api/app/agent_tools.py REGISTRY). The metadata (name/description/params)
 * lives in voiceTools.json; the bodies live here, keyed by a literal union. The
 * `Record<VoiceToolName, VoiceExecutor>` return type is the compile-time parity guard: a missing or
 * misspelled executor fails `tsc` (npm run build), and the Python parity test ties the union <->
 * json <-> registry. Execution stays client-side (REST via apiClient + map-store mutations).
 *
 * KEEP IN SYNC: adding/removing a voice tool means editing voiceTools.json AND this union + map.
 */
export type VoiceToolName =
  | "find_sites"
  | "focus_map"
  | "check_affordability"
  | "explain_parcel"
  | "grid_context"
  | "focus_parcel"
  | "export_pdf"
  | "set_map_view"
  | "zoom_map";

export type VoiceExecutor = (args: Record<string, unknown>) => Promise<unknown>;

/** The slice of the map store + helpers the voice executors need (passed in at build time). */
export interface VoiceCtx {
  flyTo: (lngLat: [number, number], zoom?: number) => void;
  setSelected: (parcel: ParcelInfo | null) => void;
  setScoreResult: (result: ScoreFeatureCollection | null) => void;
  setScoreStatus: (status: ScoreStatus, error?: string | null) => void;
  setUseCase: (useCase: UseCase) => void;
  setLayerVisible: (id: string, visible: boolean) => void;
  setBasemap: (basemap: BasemapId) => void;
  zoomByPercent: (direction: ZoomDirection, percent: number) => void;
  layers: LayerStateMap;
  scoreResult: ScoreFeatureCollection | null;
  useCase: UseCase;
  captureMapSnapshot: () => string | null;
}

export function makeVoiceExecutors(ctx: VoiceCtx): Record<VoiceToolName, VoiceExecutor> {
  const {
    flyTo,
    setSelected,
    setScoreResult,
    setScoreStatus,
    setUseCase,
    setLayerVisible,
    setBasemap,
    zoomByPercent,
    layers,
    scoreResult,
    useCase,
    captureMapSnapshot,
  } = ctx;

  return {
    find_sites: async (args) => {
      const place = String(args.place ?? "");
      const wantedUseCase: UseCase = args.use_case === "data_center" ? "data_center" : "utility_solar";
      const resolved = resolvePlace(place);
      if (!resolved) return { error: `No data for "${place}".`, known_places: PLACE_LABELS };
      setUseCase(wantedUseCase);
      setScoreStatus("scoring");
      try {
        const fc = await apiClient.score({ geometry: resolved.geometry, use_case: wantedUseCase });
        setScoreResult(fc);
        setScoreStatus("done");
        flyTo(resolved.center, 10);
        const feats = fc.features ?? [];
        return {
          place: resolved.label,
          use_case: wantedUseCase,
          count: feats.length,
          top: feats.slice(0, 3).map((f) => ({
            id: f.properties.id,
            apn: f.properties.apn,
            score: Math.round(f.properties.score),
          })),
        };
      } catch {
        setScoreStatus("error", "Scoring failed.");
        return { error: "Scoring failed for that area." };
      }
    },

    focus_map: async (args) => {
      const resolved = resolvePlace(String(args.place ?? ""));
      if (!resolved) return { error: "Unknown place.", known_places: PLACE_LABELS };
      flyTo(resolved.center, 11);
      return { ok: true, place: resolved.label };
    },

    check_affordability: async () => {
      try {
        const a = await apiClient.affordability();
        if (a.available === false) return { error: a.error ?? "Affordability data is unavailable right now." };
        return {
          geography: a.geography,
          band: a.affordability_band,
          affordability_score: a.affordability_score,
          median_home_value_usd: a.median_home_value_usd,
          price_trend_yoy_pct: a.price_trend_yoy_pct,
          note: "County-level estimate for Kern from free public data.",
        };
      } catch {
        return { error: "Couldn't reach the affordability service." };
      }
    },

    explain_parcel: async (args) => {
      const id = Number(args.parcel_id);
      if (!Number.isFinite(id)) return { error: "I need a parcel id from the ranked results first." };
      const wantedUseCase: UseCase = args.use_case === "data_center" ? "data_center" : "utility_solar";
      try {
        const e = await apiClient.explain(id, wantedUseCase);
        return {
          parcel_id: e.parcel_id,
          apn: e.apn,
          score: Math.round(e.score),
          excluded: e.excluded,
          top_factors: e.factors
            .slice(0, 3)
            .map((f) => ({ label: f.label, contribution: Math.round(f.contribution) })),
        };
      } catch {
        return { error: `I couldn't find parcel ${id}.` };
      }
    },

    grid_context: async () => {
      try {
        const c = await apiClient.context();
        return {
          county: c.county,
          total_mw: c.total?.total_mw ?? null,
          n_projects: c.total?.n_projects ?? null,
          by_type: c.by_type.slice(0, 3).map((t) => ({ tech: t.key, mw: t.total_mw })),
        };
      } catch {
        return { error: "Couldn't load the grid queue summary." };
      }
    },

    focus_parcel: async (args) => {
      const id = Number(args.parcel_id);
      if (!Number.isFinite(id)) return { error: "I need a parcel id from the ranked results first." };
      try {
        const e = await apiClient.explain(id, useCase);
        if (!e.centroid) return { error: `Parcel ${id} has no location.` };
        setSelected({ id: e.parcel_id, apn: e.apn, acres: e.acres });
        flyTo(e.centroid, 15);
        return { ok: true, parcel_id: e.parcel_id, apn: e.apn };
      } catch {
        return { error: `I couldn't find parcel ${id}.` };
      }
    },

    export_pdf: async (args) => {
      const ids = String(args.parcel_ids ?? "")
        .split(/[,;\s]+/)
        .map((t) => t.trim())
        .filter(Boolean)
        .map(Number)
        .filter((n) => Number.isFinite(n));
      try {
        const r = await generateParcelsReport({
          ids,
          result: scoreResult,
          useCase,
          snapshot: captureMapSnapshot(),
        });
        if (r.count === 0) return { error: "No scored parcels to export yet — find sites first." };
        return { ok: true, exported: r.count, requested: r.requested };
      } catch {
        return { error: "Couldn't generate the PDF." };
      }
    },

    set_map_view: async (args) => {
      // Mirror of the text agent's set_map_view: toggle data layers and/or switch the basemap,
      // then hand back a compact summary so the model can speak what changed. Runs entirely on the
      // map store (no engine call) — the same path the Layers/Basemap controls use.
      const show = resolveLayerNames(String(args.show ?? ""));
      const hide = resolveLayerNames(String(args.hide ?? ""));
      const unknownLayers = [...show.unknown, ...hide.unknown];
      if (unknownLayers.length) {
        return {
          error: `I don't have a layer called ${unknownLayers.join(", ")}. Try parcels, transmission, substations, flood, or suitability score.`,
        };
      }
      // "Show wins": a layer in both lists is shown, not hidden — so "focus on X" = show X + hide all.
      const hideIds = hide.ids.filter((id) => !show.ids.includes(id));
      const bm = resolveBasemapMode(String(args.basemap ?? "keep"));
      if (bm.unknown) {
        return { error: `I don't know a map mode called "${String(args.basemap)}". Try satellite, streets, light, dark, or auto.` };
      }
      if (!show.ids.length && !hideIds.length && bm.id === null) {
        return { error: "Tell me which layer to show or hide, or which map mode to switch to." };
      }
      hideIds.forEach((id) => setLayerVisible(id, false));
      show.ids.forEach((id) => setLayerVisible(id, true));
      if (bm.id) setBasemap(bm.id);
      // Resulting visible set, computed from current state + this change (setState is async, so we
      // can't read `layers` back immediately).
      const visible = new Set(Object.entries(layers).filter(([, t]) => t.visible).map(([id]) => id));
      hideIds.forEach((id) => visible.delete(id));
      show.ids.forEach((id) => visible.add(id));
      return {
        ok: true,
        shown: show.ids,
        hidden: hideIds,
        basemap: bm.id ?? undefined,
        layers_visible: LAYERS.filter((d) => visible.has(d.id)).map((d) => d.id),
      };
    },

    zoom_map: async (args) => {
      // Relative zoom: nudge the live map in/out by a percentage of the current view. The store's
      // zoomByPercent converts the percent to a zoom-level delta and applies it to the real map.
      const dir = String(args.direction ?? "").trim().toLowerCase();
      if (dir !== "in" && dir !== "out") return { error: "Tell me whether to zoom in or out." };
      const raw = Number(args.percent);
      const percent = Number.isFinite(raw) && raw > 0 ? raw : ZOOM_PERCENT_DEFAULT;
      zoomByPercent(dir as ZoomDirection, percent);
      return { ok: true, direction: dir, percent: Math.round(percent) };
    },
  };
}
