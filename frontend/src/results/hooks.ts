import { useEffect, useState } from "react";

import { ApiError, apiClient } from "../api/client";
import type { ContextResponse, ExplainResponse, UseCase } from "../api/client";

/** Fetch the per-factor breakdown for the selected parcel (GEO-25 detail), abortable. */
export function useExplain(parcelId: number | string | null, useCase: UseCase) {
  const [data, setData] = useState<ExplainResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (parcelId == null) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setData(null); // clear the previous parcel's breakdown so the panel never shows stale numbers
    setLoading(true);
    setError(null);
    apiClient
      .explain(parcelId, useCase, controller.signal)
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof ApiError ? err.message : "Could not load parcel detail.");
        setLoading(false);
      });
    return () => controller.abort();
  }, [parcelId, useCase]);

  return { data, loading, error };
}

/** Fetch the CAISO Kern queue context once (GEO-25 banner). Silent on failure (optional). */
export function useContextSummary(): ContextResponse | null {
  const [data, setData] = useState<ContextResponse | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    apiClient
      .context(controller.signal)
      .then(setData)
      .catch(() => {
        /* context is informational; ignore errors */
      });
    return () => controller.abort();
  }, []);
  return data;
}
