import type { ContextResponse, GeoJsonGeometry, UseCase } from "../api/client";
import type { ParsedRequest } from "./types";

/**
 * Mock-agent helpers (GEO-27). Until the live `/api/agent` lands (GEO-21), the chat parses the
 * request and drives the REAL scoring pipeline (resolve a Kern place → set the drawn area + use
 * case → `useScoring` scores it via the backend). Only the narration is mocked; the map/list
 * updates are real. Resolution is a small local gazetteer (no network), mirroring the backend's
 * `agent_tools.resolve_area` (FR-A5).
 */

const CENTERS: Record<string, { label: string; lng: number; lat: number }> = {
  bakersfield: { label: "Bakersfield", lng: -119.018, lat: 35.373 },
  mojave: { label: "Mojave", lng: -118.174, lat: 35.052 },
  tehachapi: { label: "Tehachapi", lng: -118.449, lat: 35.132 },
  ridgecrest: { label: "Ridgecrest", lng: -117.671, lat: 35.622 },
  delano: { label: "Delano", lng: -119.247, lat: 35.769 },
  taft: { label: "Taft", lng: -119.456, lat: 35.142 },
  "california city": { label: "California City", lng: -117.986, lat: 35.126 },
  rosamond: { label: "Rosamond", lng: -118.163, lat: 34.864 },
  shafter: { label: "Shafter", lng: -119.272, lat: 35.501 },
};

const PAD = 0.06;

export const SUGGESTIONS = [
  "Best solar sites near Mojave",
  "Data center sites in Bakersfield",
  "Grid queue context",
];

export function placeGeometry(key: string): GeoJsonGeometry {
  const c = CENTERS[key];
  const x0 = c.lng - PAD;
  const y0 = c.lat - PAD;
  const x1 = c.lng + PAD;
  const y1 = c.lat + PAD;
  return {
    type: "Polygon",
    coordinates: [[
      [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0],
    ]],
  };
}

export function parseRequest(raw: string): ParsedRequest {
  const text = raw.toLowerCase();

  let useCase: UseCase | undefined;
  if (/data\s*-?\s*cent|datacenter|\bdc\b/.test(text)) useCase = "data_center";
  else if (/solar|\bpv\b|panel|photovolta/.test(text)) useCase = "utility_solar";

  const wantsContext = /grid|queue|caiso|congest|context|interconnect/.test(text);

  // Whole-word place match, longest (most specific) name wins.
  let place: string | undefined;
  let label: string | undefined;
  for (const [key, info] of Object.entries(CENTERS)) {
    if (new RegExp(`\\b${key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(text)) {
      if (!place || key.length > place.length) {
        place = key;
        label = info.label;
      }
    }
  }

  return {
    place,
    label,
    geometry: place ? placeGeometry(place) : undefined,
    useCase,
    wantsContext,
  };
}

export function narrateContext(ctx: ContextResponse): string {
  const t = ctx.total;
  const top = ctx.by_type[0];
  const topText = top?.key
    ? ` The largest technology in the queue is ${top.key} at ~${Math.round(top.total_mw ?? 0).toLocaleString()} MW.`
    : "";
  return (
    `CAISO ${ctx.county} queue: ${t.n_projects ?? 0} projects totalling ~${Math.round(
      t.total_mw ?? 0,
    ).toLocaleString()} MW (~${Math.round(t.active_total_mw ?? 0).toLocaleString()} MW active).` +
    topText +
    " This is grid context only — it doesn't change parcel scores."
  );
}
