import { apiClient } from "../api/client";
import type { ContextResponse, ScoreFeatureCollection, UseCase } from "../api/client";
import type { ParcelInfo } from "../map/MapContext";
import { exportParcelsPdf } from "./pdf";

/** Cap so an over-eager "PDF everything" can't fire hundreds of /api/explain calls or a huge doc. */
const MAX_PARCELS = 25;

export interface ParcelReportResult {
  count: number;
  requested: number;
  capped: boolean;
}

/**
 * Build a multi-parcel PDF report from the agent's `export_pdf` request (GEO-41).
 *
 * Resolves which parcels to include from the current ranked results (all of them when ``ids`` is
 * empty, else just the matching ids), fetches each parcel's per-factor breakdown (/api/explain) and
 * the shared grid context once, then hands off to {@link exportParcelsPdf}. Used by BOTH the text
 * agent (via the SSE `exportPdf` result) and the voice agent's `export_pdf` tool, so the two stay in
 * parity. Returns counts so the caller can tell the user what happened. Throws only on a hard
 * failure; a missing explain/context degrades gracefully inside the PDF.
 */
export async function generateParcelsReport(args: {
  ids: number[];
  result: ScoreFeatureCollection | null;
  useCase: UseCase;
  snapshot: string | null;
}): Promise<ParcelReportResult> {
  const { ids, result, useCase, snapshot } = args;
  const feats = result?.features ?? [];
  const wanted = ids.length > 0 ? feats.filter((f) => ids.includes(Number(f.properties.id))) : feats;
  const requested = wanted.length;
  const chosen = wanted.slice(0, MAX_PARCELS);
  if (chosen.length === 0) return { count: 0, requested, capped: false };

  let context: ContextResponse | null = null;
  try {
    context = await apiClient.context();
  } catch {
    context = null; // grid context is optional; the PDF notes it's unavailable
  }

  const parcels = await Promise.all(
    chosen.map(async (f) => {
      const selected: ParcelInfo = {
        id: f.properties.id,
        apn: f.properties.apn,
        acres: f.properties.acres,
      };
      try {
        return { selected, explain: await apiClient.explain(f.properties.id, useCase) };
      } catch {
        return { selected, explain: null }; // the PDF prints a "breakdown unavailable" note
      }
    }),
  );

  await exportParcelsPdf({ parcels, useCase, context, snapshot });
  return { count: parcels.length, requested, capped: requested > chosen.length };
}
