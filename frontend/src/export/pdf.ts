import type { jsPDF } from "jspdf";

import type { ContextResponse, ExplainResponse, UseCase } from "../api/client";
import type { ParcelInfo } from "../map/MapContext";
import { fmtAcres, fmtNum, whyThisRank } from "../results/format";
import { exportFilename } from "./download";

/**
 * Per-parcel PDF one-pager (GEO-31 #5).
 *
 * Title (APN/id + score) → a map snapshot PNG (from the WebGL canvas, requires
 * preserveDrawingBuffer) → the per-factor breakdown (from /api/explain) → CAISO grid context.
 * jsPDF is dynamic-imported so its ~weight stays out of the initial bundle. Degrades gracefully:
 * a missing snapshot prints a note, missing explain/context simply omit those sections.
 */
export interface ParcelPdfData {
  selected: ParcelInfo;
  useCase: UseCase;
  explain: ExplainResponse | null;
  context: ContextResponse | null;
  snapshot: string | null;
}

function imageSize(dataUrl: string): Promise<{ w: number; h: number } | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve({ w: img.naturalWidth, h: img.naturalHeight });
    img.onerror = () => resolve(null);
    img.src = dataUrl;
  });
}

export async function exportParcelPdf(data: ParcelPdfData): Promise<void> {
  const { selected, useCase, explain, context, snapshot } = data;
  const { jsPDF } = await import("jspdf");
  const doc = new jsPDF({ unit: "mm", format: "a4" });

  const pageW = doc.internal.pageSize.getWidth();
  const margin = 16;
  const contentW = pageW - margin * 2;
  let y = margin;

  const name = selected.apn ?? `Parcel ${selected.id}`;
  doc.setFont("helvetica", "bold");
  doc.setFontSize(18);
  doc.text(name, margin, y);
  y += 7;

  doc.setFont("helvetica", "normal");
  doc.setFontSize(10);
  doc.setTextColor(110);
  const ucLabel = useCase === "data_center" ? "Data center" : "Utility solar";
  const scoreText = explain ? `Suitability ${explain.score.toFixed(0)} / 100` : "Score unavailable";
  doc.text(
    `ID ${selected.id}  ·  ${scoreText}  ·  ${ucLabel}  ·  ${fmtAcres(explain?.acres ?? selected.acres)}` +
      (explain?.zoning_class ? `  ·  ${explain.zoning_class}` : ""),
    margin,
    y,
  );
  doc.setTextColor(0);
  y += 8;

  // --- Map snapshot --------------------------------------------------------
  if (snapshot) {
    const size = await imageSize(snapshot);
    if (size && size.w > 0 && size.h > 0) {
      const imgH = Math.min(95, (contentW * size.h) / size.w);
      const imgW = (imgH * size.w) / size.h;
      try {
        doc.addImage(snapshot, "PNG", margin, y, imgW, imgH);
        doc.setDrawColor(200);
        doc.rect(margin, y, imgW, imgH);
        y += imgH + 8;
      } catch {
        y = printNote(doc, "Map snapshot unavailable.", margin, y);
      }
    } else {
      y = printNote(doc, "Map snapshot unavailable.", margin, y);
    }
  } else {
    y = printNote(doc, "Map snapshot unavailable.", margin, y);
  }

  // --- Factor breakdown ----------------------------------------------------
  doc.setFont("helvetica", "bold");
  doc.setFontSize(12);
  doc.text("Scoring breakdown", margin, y);
  y += 6;

  if (explain) {
    doc.setFont("helvetica", "normal");
    doc.setFontSize(9);
    doc.setTextColor(90);
    const why = doc.splitTextToSize(whyThisRank(explain.factors), contentW) as string[];
    doc.text(why, margin, y);
    y += why.length * 4 + 3;
    doc.setTextColor(0);

    // Column header.
    doc.setFont("helvetica", "bold");
    doc.setFontSize(9);
    doc.text("Factor", margin, y);
    doc.text("Value", margin + 70, y);
    doc.text("Weight", margin + 110, y);
    doc.text("Points", margin + 140, y);
    y += 2;
    doc.setDrawColor(220);
    doc.line(margin, y, margin + contentW, y);
    y += 4;

    doc.setFont("helvetica", "normal");
    for (const f of explain.factors) {
      const raw = f.known ? fmtNum(f.raw, f.unit) : "unknown";
      doc.text(String(f.label), margin, y);
      doc.text(raw, margin + 70, y);
      doc.text(`${(f.weight * 100).toFixed(0)}%`, margin + 110, y);
      doc.text(`+${f.contribution.toFixed(1)}`, margin + 140, y);
      y += 5;
    }
    y += 4;
  } else {
    y = printNote(doc, "Per-factor breakdown unavailable (the explain service could not be reached).", margin, y);
  }

  // --- CAISO grid context --------------------------------------------------
  doc.setFont("helvetica", "bold");
  doc.setFontSize(12);
  doc.text("Grid context (CAISO queue)", margin, y);
  y += 6;
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  if (context) {
    const t = context.total;
    const top = context.by_type[0];
    const lines = [
      `${context.county}: ${t.n_projects ?? 0} projects · ${Math.round(t.total_mw ?? 0).toLocaleString()} MW ` +
        `(${Math.round(t.active_total_mw ?? 0).toLocaleString()} MW active).`,
    ];
    if (top?.key) lines.push(`Largest technology in queue: ${top.key} (~${Math.round(top.total_mw ?? 0).toLocaleString()} MW).`);
    doc.setTextColor(90);
    doc.text(doc.splitTextToSize(lines.join(" "), contentW) as string[], margin, y);
    doc.setTextColor(0);
  } else {
    printNote(doc, "Grid context unavailable.", margin, y);
  }

  // --- Footer --------------------------------------------------------------
  const pageH = doc.internal.pageSize.getHeight();
  doc.setFontSize(8);
  doc.setTextColor(150);
  doc.text(`Generated ${new Date().toLocaleString()} · Site-Selection App`, margin, pageH - 10);

  const idPart = String(selected.id).replace(/[^a-zA-Z0-9-]+/g, "-").slice(0, 24);
  doc.save(exportFilename(useCase, "pdf", `parcel-${idPart}`));
}

function printNote(doc: jsPDF, text: string, x: number, y: number): number {
  doc.setFont("helvetica", "italic");
  doc.setFontSize(9);
  doc.setTextColor(130);
  doc.text(text, x, y);
  doc.setTextColor(0);
  doc.setFont("helvetica", "normal");
  return y + 8;
}
