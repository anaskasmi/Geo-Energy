import { useEffect } from "react";
import { area } from "@turf/area";

import { apiClient } from "../api/client";
import { describeError } from "../api/errors";
import { withRetry } from "../api/retry";
import { useMapStore } from "../map/useMapStore";

/** Debounce window for re-scoring while the polygon is edited (GEO-24: 250–400 ms). */
const DEBOUNCE_MS = 300;
/** Cap returned parcels; the results panel paginates client-side. */
const SCORE_LIMIT = 500;
/**
 * Client-side area guard (GEO-32 #10): refuse to score an unreasonably large area before the
 * request, with the same "draw a smaller area" message the API's 413/422 would produce. Set
 * generously (~5,000 km²) so normal city-scale searches never trip it.
 */
const MAX_AREA_SQM = 5_000_000_000;

/**
 * Score the drawn polygon whenever it (or the use case / weights / a retry) changes (GEO-24).
 *
 * Debounced so terra-draw's per-vertex `change` events don't spam the API, abortable so a
 * superseded request never overwrites a newer result, and wrapped in bounded exponential backoff
 * (GEO-32 #11) so a transient network/5xx/429 self-recovers; a deterministic 4xx (e.g. bad
 * polygon) fails fast. The previous result stays on screen while a new score is in flight, and an
 * error keeps the old result while surfacing a specific, actionable message. Mount this ONCE.
 */
export function useScoring(): void {
  const { drawnPolygon, useCase, weights, scoreNonce, setScoreResult, setScoreStatus } =
    useMapStore();

  useEffect(() => {
    if (!drawnPolygon) {
      setScoreResult(null);
      setScoreStatus("idle");
      return;
    }

    // Client-side area guard before any network round-trip.
    let sqm = 0;
    try {
      sqm = area(drawnPolygon as never);
    } catch {
      sqm = 0;
    }
    if (sqm > MAX_AREA_SQM) {
      setScoreStatus(
        "error",
        "The drawn area is too large to score. Draw a smaller area and try again.",
        "smaller",
      );
      return;
    }

    const controller = new AbortController();
    setScoreStatus("scoring");
    const timer = setTimeout(() => {
      withRetry(
        (signal) =>
          apiClient.score(
            {
              geometry: drawnPolygon,
              use_case: useCase,
              limit: SCORE_LIMIT,
              ...(weights ? { weights } : {}),
            },
            signal,
          ),
        { signal: controller.signal },
      )
        .then((fc) => {
          setScoreResult(fc);
          setScoreStatus("done");
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return; // superseded by a newer request
          const info = describeError(err);
          setScoreStatus("error", info.detail, info.action);
        });
    }, DEBOUNCE_MS);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [drawnPolygon, useCase, weights, scoreNonce, setScoreResult, setScoreStatus]);
}
