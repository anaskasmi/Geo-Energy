import type { ScoredFeature } from "../api/client";

/** Display helpers + plain-language summaries for the results panel (GEO-25). */

/** Coerce a value to a finite number, else null — so a formatter never throws on a stray
 *  string/NaN (the API contract is numbers, but display code stays robust regardless). */
function num(v: unknown): number | null {
  const n = typeof v === "number" ? v : v == null ? NaN : Number(v);
  return Number.isFinite(n) ? n : null;
}

export function fmtAcres(value: number | null | undefined): string {
  const n = num(value);
  return n == null ? "—" : `${n.toFixed(n >= 100 ? 0 : 1)} ac`;
}

export function fmtMeters(value: number | null | undefined): string {
  const n = num(value);
  if (n == null) return "—";
  return n >= 1000 ? `${(n / 1000).toFixed(1)} km` : `${Math.round(n)} m`;
}

export function fmtKv(value: number | null | undefined): string {
  const n = num(value);
  return n == null ? "n/a" : `${Math.round(n)} kV`;
}

export function fmtNum(value: number | null | undefined, unit = ""): string {
  const n = num(value);
  if (n == null) return "—";
  const v = Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(1);
  return unit ? `${v} ${unit}` : v;
}

/** A couple of quick highlight tags for a results-list row (illustrative; the authoritative
 *  per-factor breakdown comes from /api/explain in the detail panel). */
export function dominantReasons(feature: ScoredFeature): string[] {
  const f = feature.properties.factors;
  const tags: string[] = [];
  if (f.ghi != null && f.ghi >= 6.0) tags.push("Strong sun");
  if (f.dist_tx_m != null && f.dist_tx_m <= 3000) tags.push("Near transmission");
  if (f.nearest_sub_kv != null && f.nearest_sub_kv >= 200) tags.push("High-voltage substation");
  if (feature.properties.acres != null && feature.properties.acres >= 100) tags.push("Large site");
  if (f.mean_slope_pct != null && f.mean_slope_pct <= 3) tags.push("Flat terrain");
  return tags.slice(0, 2);
}

/** One-sentence "why this rank" from an explain breakdown (top contributors). */
export function whyThisRank(factors: { label: string; contribution: number; known: boolean }[]): string {
  const ranked = [...factors].sort((a, b) => b.contribution - a.contribution);
  const top = ranked.slice(0, 2).map((f) => f.label.toLowerCase());
  if (top.length === 0) return "No scoring factors available.";
  const lead = top.join(" and ");
  const unknowns = factors.filter((f) => !f.known).map((f) => f.label.toLowerCase());
  const caveat = unknowns.length ? ` Data was unavailable for ${unknowns.join(", ")} (scored neutrally).` : "";
  return `Driven mainly by ${lead}.${caveat}`;
}

export const SORTS: { key: string; label: string; get: (f: ScoredFeature) => number; dir: 1 | -1 }[] = [
  { key: "score", label: "Score (high → low)", get: (f) => f.properties.score, dir: -1 },
  { key: "acres", label: "Acreage (large → small)", get: (f) => f.properties.acres ?? 0, dir: -1 },
  { key: "slope", label: "Slope (flat → steep)", get: (f) => f.properties.factors.mean_slope_pct ?? Infinity, dir: 1 },
  { key: "tx", label: "Grid distance (near → far)", get: (f) => f.properties.factors.dist_tx_m ?? Infinity, dir: 1 },
];
