import { useEffect } from "react";

import { ApiError, apiClient } from "../api/client";
import { useMapStore } from "../map/useMapStore";

/** Debounce window for re-scoring while the polygon is edited (GEO-24: 250–400 ms). */
const DEBOUNCE_MS = 300;
/** Cap returned parcels; the results panel paginates client-side. */
const SCORE_LIMIT = 500;

/**
 * Score the drawn polygon whenever it (or the use case) changes (GEO-24).
 *
 * Debounced so terra-draw's per-vertex `change` events don't spam the API, and abortable so a
 * superseded request never overwrites a newer result. The previous result stays on screen while
 * a new score is in flight (optimistic), and an error keeps the old result while surfacing the
 * message. Mount this ONCE near the app root.
 */
export function useScoring(): void {
  const { drawnPolygon, useCase, setScoreResult, setScoreStatus } = useMapStore();

  useEffect(() => {
    if (!drawnPolygon) {
      setScoreResult(null);
      setScoreStatus("idle");
      return;
    }
    const controller = new AbortController();
    setScoreStatus("scoring");
    const timer = setTimeout(() => {
      apiClient
        .score({ geometry: drawnPolygon, use_case: useCase, limit: SCORE_LIMIT }, controller.signal)
        .then((fc) => {
          setScoreResult(fc);
          setScoreStatus("done");
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return; // superseded by a newer request
          const message =
            err instanceof ApiError ? err.message : "Could not reach the scoring service.";
          setScoreStatus("error", message);
        });
    }, DEBOUNCE_MS);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [drawnPolygon, useCase, setScoreResult, setScoreStatus]);
}
