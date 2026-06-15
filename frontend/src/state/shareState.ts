import type { GeoJsonGeometry, UseCase } from "../api/client";

/**
 * Compact, round-trippable encoding of the analysis state for URL sharing + local save (GEO-31).
 *
 * The captured state is: use case + drawn geometry + (optional) weights + the selected parcel.
 * It's serialized to a terse JSON with coordinates rounded to ~6 decimals (≈11 cm — far finer
 * than parcel boundaries), then base64url-encoded so it survives in a URL hash without escaping.
 * Compactness is deliberate: a hand-rolled encoder (no compression dependency) keeps the bundle
 * lean while a hashed payload of a few hundred coordinates stays well within URL limits.
 *
 * Wire shape (keys kept short): { u, g, w?, s? }
 *   u: use-case code  (0 = utility_solar, 1 = data_center)
 *   g: geometry       ({ t: "Polygon" | "MultiPolygon", c: <rounded coordinates> } | null)
 *   w: weights        (Record<string, number>, omitted when null)
 *   s: selected id    (number | string, omitted when null)
 */

export interface ShareState {
  useCase: UseCase;
  geometry: GeoJsonGeometry | null;
  weights: Record<string, number> | null;
  selectedId: number | string | null;
}

const COORD_PRECISION = 1e6;
const USE_CASES: UseCase[] = ["utility_solar", "data_center"];

/** Recursively round every number in a coordinate array to COORD_PRECISION decimals. */
function roundCoords(value: unknown): unknown {
  if (typeof value === "number") return Math.round(value * COORD_PRECISION) / COORD_PRECISION;
  if (Array.isArray(value)) return value.map(roundCoords);
  return value;
}

// --- base64url (no padding) over a UTF-8 JSON string -------------------------
function toBase64Url(json: string): string {
  const b64 = btoa(unescape(encodeURIComponent(json)));
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(payload: string): string {
  const b64 = payload.replace(/-/g, "+").replace(/_/g, "/");
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  return decodeURIComponent(escape(atob(padded)));
}

interface WireState {
  u: number;
  g: { t: string; c: unknown } | null;
  w?: Record<string, number>;
  s?: number | string;
}

/** Encode a ShareState to the compact base64url payload (the `#s=` value). */
export function encodeShareState(state: ShareState): string {
  const wire: WireState = {
    u: Math.max(0, USE_CASES.indexOf(state.useCase)),
    g: state.geometry
      ? { t: state.geometry.type, c: roundCoords(state.geometry.coordinates) }
      : null,
  };
  if (state.weights && Object.keys(state.weights).length > 0) wire.w = state.weights;
  if (state.selectedId != null) wire.s = state.selectedId;
  return toBase64Url(JSON.stringify(wire));
}

/** Decode a base64url payload back to a ShareState, or null if missing/garbage. */
export function decodeShareState(payload: string | null | undefined): ShareState | null {
  if (!payload) return null;
  try {
    const wire = JSON.parse(fromBase64Url(payload)) as Partial<WireState>;
    if (wire == null || typeof wire !== "object") return null;
    const useCase = USE_CASES[wire.u ?? 0] ?? "utility_solar";
    let geometry: GeoJsonGeometry | null = null;
    if (wire.g && typeof wire.g.t === "string" && wire.g.c != null) {
      geometry = { type: wire.g.t, coordinates: wire.g.c };
    }
    const weights =
      wire.w && typeof wire.w === "object" ? (wire.w as Record<string, number>) : null;
    const selectedId =
      typeof wire.s === "number" || typeof wire.s === "string" ? wire.s : null;
    // A state with neither geometry nor selection carries nothing useful.
    if (!geometry && selectedId == null) return null;
    return { useCase, geometry, weights, selectedId };
  } catch {
    return null;
  }
}

/** Best-effort [lng, lat] centroid of a polygon/multipolygon (for fly-to on hydration). */
export function geometryCenter(geometry: GeoJsonGeometry | null): [number, number] | null {
  if (!geometry) return null;
  let sumX = 0;
  let sumY = 0;
  let n = 0;
  const visit = (node: unknown) => {
    if (!Array.isArray(node)) return;
    const x = node[0];
    const y = node[1];
    if (typeof x === "number" && typeof y === "number") {
      sumX += x;
      sumY += y;
      n += 1;
    } else {
      node.forEach(visit);
    }
  };
  visit(geometry.coordinates);
  return n > 0 ? [sumX / n, sumY / n] : null;
}
