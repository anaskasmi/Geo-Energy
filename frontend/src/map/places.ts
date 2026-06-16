import type { GeoJsonGeometry } from "../api/client";

/**
 * Small local Kern-County gazetteer for the "try an example" CTA (GEO-32 #8): a curated set of city
 * centers turned into a square search polygon to seed a drawn area. This is a UI convenience only —
 * the live agent resolves places server-side (api/app/agent_tools.resolve_area), not from this list.
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

function squareAround(lng: number, lat: number): GeoJsonGeometry {
  const x0 = lng - PAD;
  const y0 = lat - PAD;
  const x1 = lng + PAD;
  const y1 = lat + PAD;
  return {
    type: "Polygon",
    coordinates: [[
      [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0],
    ]],
  };
}

/** A square Polygon (EPSG:4326) around a known Kern place center. */
export function placeGeometry(key: string): GeoJsonGeometry {
  const c = CENTERS[key];
  return squareAround(c.lng, c.lat);
}

/** Human-readable labels of the known places (used to brief the voice agent). */
export const PLACE_LABELS: string[] = Object.values(CENTERS).map((c) => c.label);

export interface ResolvedPlace {
  key: string;
  label: string;
  center: [number, number];
  geometry: GeoJsonGeometry;
}

/**
 * Fuzzy-resolve a free-text place name to a known Kern center (voice mode, GEO-40). Tolerant of
 * case/whitespace and partial names ("cal city" → California City). Returns null when nothing in the
 * local gazetteer matches, so the caller can speak a graceful "I don't know that area".
 */
export function resolvePlace(query: string): ResolvedPlace | null {
  const q = query.trim().toLowerCase();
  if (!q) return null;
  const entries = Object.entries(CENTERS);
  const exact = entries.find(([key, c]) => key === q || c.label.toLowerCase() === q);
  const partial =
    exact ??
    entries.find(([key, c]) => {
      const label = c.label.toLowerCase();
      return key.includes(q) || q.includes(key) || label.includes(q) || q.includes(label);
    });
  if (!partial) return null;
  const [key, c] = partial;
  return { key, label: c.label, center: [c.lng, c.lat], geometry: squareAround(c.lng, c.lat) };
}
