import type { ScoreFeatureCollection } from "../api/client";
import type { BasemapId } from "../theme/basemap";

export type ChatRole = "user" | "assistant";

/** A clickable reference to a scored parcel, surfaced under an assistant message (GEO-27). */
export interface ParcelRef {
  id: number | string;
  apn: string | null;
  score: number;
  centroid: [number, number] | null;
}

/** Area-level land-affordability summary from the `check_affordability` tool (GEO-41). */
export interface Affordability {
  geography: string;
  median_home_value_usd: number | null;
  acs_vintage: string | null;
  hpi_index: number | null;
  price_trend_yoy_pct: number | null;
  hpi_as_of: string | null;
  /** 0..1, higher = cheaper land; null when no median was available. */
  affordability_score: number | null;
  affordability_band: string; // "affordable" | "moderate" | "expensive" | "unknown"
  sources: string[];
  note: string;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  /** Clickable parcel references (rendered as chips below the bubble). */
  refs?: ParcelRef[];
  /** Land-affordability summary card (rendered below the bubble) when the agent ran the check. */
  affordability?: Affordability;
  /** True while the text is still streaming in. */
  streaming?: boolean;
  /** The current tool phase (a `step` event), shown while the agent works; cleared once text/result arrives. */
  phase?: string;
}

/**
 * The streamed event contract the LIVE `/api/agent` (GEO-21 — Gemini via Pydantic AI) emits over
 * SSE. Mirrors the server frames in api/app/agent.py: `step | token | result | error | done`.
 */
/** The agent asked the UI to zoom/select a specific parcel (focus_parcel tool). */
export interface FocusParcel {
  parcel_id: number | string;
  apn: string | null;
  centroid: [number, number];
}

/** The agent asked the UI to generate a PDF report (export_pdf tool); empty ids = all shown. */
export interface ExportPdfRequest {
  parcel_ids: number[];
}

/** The agent asked the UI to toggle layers and/or switch the basemap (set_map_view tool). */
export interface MapViewRequest {
  /** Canonical layer ids to turn ON. */
  show: string[];
  /** Canonical layer ids to turn OFF. */
  hide: string[];
  /** Basemap to switch to, or null to leave the current basemap unchanged. */
  basemap: BasemapId | null;
}

export type AgentStreamEvent =
  | { type: "step"; phase: string; tool: string }
  | { type: "token"; text: string }
  | {
      type: "result";
      featureCollection?: ScoreFeatureCollection;
      area?: string;
      affordability?: Affordability;
      focus?: FocusParcel;
      exportPdf?: ExportPdfRequest;
      mapView?: MapViewRequest;
    }
  | { type: "error"; message: string }
  | { type: "done" };
