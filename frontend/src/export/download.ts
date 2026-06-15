import type { UseCase } from "../api/client";

/** Trigger a browser download of a Blob under `filename` (GEO-31 exports). */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoke after the click has been handled so the download isn't cancelled.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** Build an export filename like `geo-energy_utility-solar_2026-06-15.geojson`. */
export function exportFilename(useCase: UseCase, ext: string, suffix?: string): string {
  const uc = useCase === "data_center" ? "data-center" : "utility-solar";
  const date = new Date().toISOString().slice(0, 10);
  const mid = suffix ? `${uc}_${suffix}` : uc;
  return `geo-energy_${mid}_${date}.${ext}`;
}
