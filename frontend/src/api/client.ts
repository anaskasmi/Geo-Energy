import { API_BASE_URL } from "../config/env";

/**
 * Minimal API client for the Site-Selection API (GEO-15/16/17).
 *
 * Reads the base URL from `import.meta.env.VITE_API_BASE_URL` (via config/env) and exposes
 * typed helpers: `health`, `score` (POST a drawn polygon → ranked parcel FeatureCollection),
 * `explain` (per-factor breakdown for one parcel), and `context` (CAISO Kern queue summary).
 * Each helper forwards an optional AbortSignal so callers can cancel in-flight requests
 * (the scoring debounce relies on this).
 */

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function buildUrl(path: string): string {
  const base = API_BASE_URL.replace(/\/+$/, "");
  const suffix = path.replace(/^\/+/, "");
  return `${base}/${suffix}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildUrl(path), {
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // non-JSON error body; keep the status line
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export interface HealthResponse {
  status: string;
  [key: string]: unknown;
}

// --- Scoring contracts (mirror api/app/serialize.py + models.py) -------------
export type UseCase = "utility_solar" | "data_center";

/** GeoJSON Polygon/MultiPolygon (EPSG:4326). */
export interface GeoJsonGeometry {
  type: string;
  coordinates: unknown;
}

export interface ScoreThresholds {
  min_acres?: number;
  max_slope_pct?: number;
  exclude_sfha?: boolean;
  apply_optional_exclusions?: boolean;
  prohibited_zoning?: string[];
}

export interface ScoreRequestBody {
  geometry: GeoJsonGeometry;
  use_case: UseCase;
  weights?: Record<string, number>;
  thresholds?: ScoreThresholds;
  limit?: number;
  offset?: number;
}

/** Raw per-factor values carried on each scored feature. */
export interface ScoreFactors {
  ghi: number | null;
  mean_slope_pct: number | null;
  dist_tx_m: number | null;
  dist_sub_m: number | null;
  nearest_sub_kv: number | null;
  poi_competition_mw: number | null;
  poi_competition_n: number | null;
  eia_nearest_m: number | null;
}

export interface ScoredFeatureProps {
  id: number | string;
  apn: string | null;
  rank: number;
  score: number;
  acres: number | null;
  zoning_class: string | null;
  sfha_flag: boolean | null;
  centroid: [number, number] | null;
  factors: ScoreFactors;
}

export interface ScoredFeature {
  type: "Feature";
  id: number | string;
  geometry: GeoJsonGeometry | null;
  properties: ScoredFeatureProps;
}

export interface ScoreMeta {
  use_case: UseCase;
  weights: Record<string, number>;
  thresholds: Record<string, unknown>;
  prohibited_zoning: string[];
  zoning_rules_available: boolean;
  limit: number;
  offset: number;
  count: number;
}

export interface ScoreFeatureCollection {
  type: "FeatureCollection";
  features: ScoredFeature[];
  meta: ScoreMeta;
}

export interface FactorBreakdown {
  key: string;
  label: string;
  unit: string;
  raw: number | null;
  normalized: number;
  weight: number;
  contribution: number;
  known: boolean;
}

export interface ExplainResponse {
  parcel_id: number | string;
  apn: string | null;
  use_case: UseCase;
  score: number;
  acres: number | null;
  zoning_class: string | null;
  centroid: [number, number] | null;
  excluded: boolean;
  exclusions: { min_acres: boolean; sfha: boolean; slope: boolean; zoning: boolean; optional: boolean };
  factors: FactorBreakdown[];
  raw: Record<string, unknown>;
}

export interface QueueSummaryItem {
  key: string | null;
  n_projects: number | null;
  total_mw: number | null;
  active_n_projects: number | null;
  active_total_mw: number | null;
}

export interface ContextResponse {
  county: string;
  total: Omit<QueueSummaryItem, "key">;
  by_type: QueueSummaryItem[];
  by_status: QueueSummaryItem[];
  note: string;
}

export const apiClient = {
  baseUrl: API_BASE_URL,
  /** Liveness/readiness probe (GEO-15 exposes /api/health). */
  health: () => request<HealthResponse>("health"),
  /** Score parcels intersecting a drawn polygon (GEO-16/17). */
  score: (body: ScoreRequestBody, signal?: AbortSignal) =>
    request<ScoreFeatureCollection>("score", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    }),
  /** Per-factor breakdown for one parcel (GEO-17). */
  explain: (parcelId: number | string, useCase: UseCase, signal?: AbortSignal) =>
    request<ExplainResponse>(`explain/${encodeURIComponent(String(parcelId))}?use_case=${useCase}`, {
      signal,
    }),
  /** CAISO Kern interconnection-queue context (GEO-17). */
  context: (signal?: AbortSignal) => request<ContextResponse>("context", { signal }),
};

export type ApiClient = typeof apiClient;
