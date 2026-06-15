import type { ExplainResponse } from "../api/client";
import type { ParcelInfo } from "../map/MapContext";
import { fmtAcres, fmtNum, whyThisRank } from "../results/format";

/**
 * Clipboard helpers (GEO-31 #6): copy a concise plain-text parcel summary.
 *
 * `copyText` prefers the async Clipboard API and falls back to a hidden-textarea + execCommand
 * for older/insecure contexts, so "copy" works wherever possible and reports success/failure.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the legacy path
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    textarea.remove();
    return ok;
  } catch {
    return false;
  }
}

const UNITS: Record<string, string> = {
  ghi: "kWh/m²/day",
  mean_slope_pct: "%",
  dist_tx_m: "m",
  dist_sub_m: "m",
  nearest_sub_kv: "kV",
};

/** Build a concise plain-text summary of the selected parcel for the clipboard. */
export function parcelSummaryText(selected: ParcelInfo, explain: ExplainResponse | null): string {
  const name = selected.apn ?? `Parcel ${selected.id}`;
  const lines: string[] = [name, `ID: ${selected.id}`];

  if (!explain) {
    lines.push(`Acres: ${fmtAcres(selected.acres)}`);
    lines.push("(scoring breakdown unavailable)");
    return lines.join("\n");
  }

  lines.push(`Suitability score: ${explain.score.toFixed(0)} / 100`);
  lines.push(`Acres: ${fmtAcres(explain.acres ?? selected.acres)}`);
  if (explain.zoning_class) lines.push(`Zoning: ${explain.zoning_class}`);
  lines.push(`Use case: ${explain.use_case === "data_center" ? "Data center" : "Utility solar"}`);
  lines.push("");
  lines.push(whyThisRank(explain.factors));
  lines.push("");
  lines.push("Top factors:");
  const top = [...explain.factors].sort((a, b) => b.contribution - a.contribution).slice(0, 5);
  for (const f of top) {
    const raw = f.known ? fmtNum(f.raw, f.unit || UNITS[f.key] || "") : "unknown";
    lines.push(`  - ${f.label}: ${raw} (+${f.contribution.toFixed(1)} pts, weight ${(f.weight * 100).toFixed(0)}%)`);
  }
  return lines.join("\n");
}
