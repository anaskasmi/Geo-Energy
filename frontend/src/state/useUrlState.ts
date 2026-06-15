import { useEffect, useRef } from "react";

import type { MapStore } from "../map/MapContext";
import { useMapStore } from "../map/useMapStore";
import { decodeShareState, encodeShareState, geometryCenter } from "./shareState";
import type { ShareState } from "./shareState";

const HASH_KEY = "s";
const WRITE_DEBOUNCE_MS = 400;

/** Read the `#s=` payload from the current location hash. */
function readHashPayload(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.hash.replace(/^#/, "")).get(HASH_KEY);
}

/** Replace (never push) the `#s=` hash with the encoded state, or strip it when empty. */
function writeHash(state: ShareState): void {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const hasContent = !!state.geometry || state.selectedId != null;
  if (hasContent) params.set(HASH_KEY, encodeShareState(state));
  else params.delete(HASH_KEY);
  const hash = params.toString();
  const url = window.location.pathname + window.location.search + (hash ? `#${hash}` : "");
  window.history.replaceState(window.history.state, "", url);
}

/** Build a full, shareable URL for the given state (the "Copy share link" target). */
export function buildShareUrl(state: ShareState): string {
  const payload = encodeShareState(state);
  return `${window.location.origin}${window.location.pathname}${window.location.search}#${HASH_KEY}=${payload}`;
}

/** The store's current shareable state (used to build links + save analyses). */
export function currentShareState(store: MapStore): ShareState {
  return {
    useCase: store.useCase,
    geometry: store.drawnPolygon,
    weights: store.weights,
    selectedId: store.selected?.id ?? null,
  };
}

/** Hydrate the store from a decoded ShareState (URL load + "Load saved analysis"). */
export function applyShareState(store: MapStore, state: ShareState): void {
  store.setUseCase(state.useCase);
  store.setWeights(state.weights);
  store.setDrawnPolygon(state.geometry);
  store.setSelected(
    state.selectedId != null ? { id: state.selectedId, apn: null, acres: null } : null,
  );
  const center = geometryCenter(state.geometry);
  if (center) window.setTimeout(() => store.flyTo(center), 400);
}

/**
 * URL-encoded state (GEO-31 #1): hydrate the store from the hash on first load, then mirror
 * use case + drawn area + weights + selection back into the hash (debounced, replaceState — no
 * history spam). Robust: a missing/garbage hash is ignored and the app starts normally. Mount
 * ONCE near the app root.
 */
export function useUrlState(): void {
  const store = useMapStore();
  const { useCase, drawnPolygon, weights, selected } = store;
  const firstWrite = useRef(true);

  // Hydrate from the hash exactly once on mount (triggers scoring via useScoring).
  useEffect(() => {
    const state = decodeShareState(readHashPayload());
    if (state) applyShareState(store, state);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Mirror store → hash (debounced). Skip the first run so we never clobber a freshly-read hash.
  useEffect(() => {
    if (firstWrite.current) {
      firstWrite.current = false;
      return;
    }
    const state: ShareState = {
      useCase,
      geometry: drawnPolygon,
      weights,
      selectedId: selected?.id ?? null,
    };
    const timer = window.setTimeout(() => writeHash(state), WRITE_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [useCase, drawnPolygon, weights, selected]);
}
