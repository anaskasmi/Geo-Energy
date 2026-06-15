import type { ScoreFeatureCollection } from "../api/client";
import { downloadBlob, exportFilename } from "./download";

/**
 * GeoJSON + CSV exporters for the scored results (GEO-31 #3, #4).
 *
 * GeoJSON: the `scoreResult` FeatureCollection, pretty-printed and downloaded as `.geojson`.
 * CSV: one row per scored parcel — rank/id/apn/score/acres/zoning/sfha + the key factors — with
 * RFC-4180 quoting (a hand-rolled encoder; no dependency needed for this fixed, simple schema).
 */

/** Export the scored FeatureCollection as a pretty-printed `.geojson` download. */
export function exportGeoJSON(result: ScoreFeatureCollection): void {
  const blob = new Blob([JSON.stringify(result, null, 2)], {
    type: "application/geo+json",
  });
  downloadBlob(blob, exportFilename(result.meta.use_case, "geojson"));
}

const CSV_COLUMNS: { header: string; get: (f: ScoreFeatureCollection["features"][number]) => unknown }[] = [
  { header: "rank", get: (f) => f.properties.rank },
  { header: "id", get: (f) => f.properties.id },
  { header: "apn", get: (f) => f.properties.apn },
  { header: "score", get: (f) => round(f.properties.score, 2) },
  { header: "acres", get: (f) => round(f.properties.acres, 2) },
  { header: "zoning_class", get: (f) => f.properties.zoning_class },
  { header: "sfha_flag", get: (f) => f.properties.sfha_flag },
  { header: "ghi", get: (f) => round(f.properties.factors.ghi, 2) },
  { header: "mean_slope_pct", get: (f) => round(f.properties.factors.mean_slope_pct, 2) },
  { header: "dist_tx_m", get: (f) => round(f.properties.factors.dist_tx_m, 1) },
  { header: "dist_sub_m", get: (f) => round(f.properties.factors.dist_sub_m, 1) },
  { header: "nearest_sub_kv", get: (f) => f.properties.factors.nearest_sub_kv },
  { header: "poi_competition_mw", get: (f) => round(f.properties.factors.poi_competition_mw, 2) },
];

function round(value: number | null | undefined, places: number): number | null {
  if (value == null || !Number.isFinite(value)) return null;
  const p = 10 ** places;
  return Math.round(value * p) / p;
}

/**
 * RFC-4180 cell escaping (quote + double internal quotes when needed) PLUS spreadsheet
 * formula-injection neutralization (CWE-1236): a cell that starts with `=`, `+`, `-`, `@`, tab
 * or CR is prefixed with a single quote so Excel/Sheets treat it as text, not a formula. Needed
 * because `apn`/`zoning_class` come from ingested external datasets, not app-controlled input.
 */
function csvCell(value: unknown): string {
  if (value == null) return "";
  let str = typeof value === "boolean" ? (value ? "true" : "false") : String(value);
  if (/^[=+\-@\t\r]/.test(str)) str = "'" + str;
  if (/[",\r\n]/.test(str)) return `"${str.replace(/"/g, '""')}"`;
  return str;
}

/** Export the scored parcels as a `.csv` download (one row per parcel). */
export function exportCSV(result: ScoreFeatureCollection): void {
  const header = CSV_COLUMNS.map((c) => c.header).join(",");
  const rows = result.features.map((f) =>
    CSV_COLUMNS.map((c) => csvCell(c.get(f))).join(","),
  );
  // Lead with a BOM so Excel reads UTF-8 correctly; CRLF line endings per RFC-4180.
  const csv = "﻿" + [header, ...rows].join("\r\n") + "\r\n";
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  downloadBlob(blob, exportFilename(result.meta.use_case, "csv"));
}
