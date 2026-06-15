import type { ShareState } from "./shareState";
import { decodeShareState, encodeShareState } from "./shareState";

/**
 * LOCAL-ONLY saved analyses (GEO-31 #2; server-side persistence deferred per review C13).
 *
 * Each saved analysis stores the same compact payload used in the share URL plus a name and a
 * timestamp, in localStorage. All access is wrapped so private-mode / disabled-storage degrades
 * gracefully: `storageAvailable()` gates the UI, and every read/write fails closed (no throw).
 */
const STORAGE_KEY = "geo.savedAnalyses.v1";

export interface SavedAnalysis {
  id: string;
  name: string;
  /** Epoch ms. */
  savedAt: number;
  /** The compact base64url share payload (decode with decodeShareState). */
  payload: string;
}

/** Whether localStorage can actually be written (false in private mode / when blocked). */
export function storageAvailable(): boolean {
  try {
    const probe = "__geo_probe__";
    localStorage.setItem(probe, "1");
    localStorage.removeItem(probe);
    return true;
  } catch {
    return false;
  }
}

export function listSavedAnalyses(): SavedAnalysis[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (a): a is SavedAnalysis =>
          !!a &&
          typeof a === "object" &&
          typeof (a as SavedAnalysis).id === "string" &&
          typeof (a as SavedAnalysis).name === "string" &&
          typeof (a as SavedAnalysis).payload === "string",
      )
      .sort((a, b) => b.savedAt - a.savedAt);
  } catch {
    return [];
  }
}

function write(list: SavedAnalysis[]): boolean {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
    return true;
  } catch {
    return false;
  }
}

/** Save (or overwrite a same-named) analysis. Returns the updated list, or null on failure. */
export function saveAnalysis(name: string, state: ShareState): SavedAnalysis[] | null {
  const trimmed = name.trim();
  if (!trimmed) return null;
  const payload = encodeShareState(state);
  const entry: SavedAnalysis = {
    id: `a${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
    name: trimmed,
    savedAt: Date.now(),
    payload,
  };
  const existing = listSavedAnalyses().filter((a) => a.name !== trimmed);
  const next = [entry, ...existing];
  return write(next) ? next : null;
}

export function deleteAnalysis(id: string): SavedAnalysis[] {
  const next = listSavedAnalyses().filter((a) => a.id !== id);
  write(next);
  return next;
}

/** Decode a saved entry's payload back to a hydratable ShareState. */
export function loadAnalysis(entry: SavedAnalysis): ShareState | null {
  return decodeShareState(entry.payload);
}
