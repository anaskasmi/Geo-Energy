import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiClient, type ScoreFeatureCollection } from "../api/client";
import { useMapStore } from "../map/useMapStore";
import { narrateContext, parseRequest } from "./mockAgent";
import type { ChatMessage, ParcelRef } from "./types";

const SCORE_LIMIT = 500;

const INTRO: ChatMessage = {
  id: "intro",
  role: "assistant",
  text:
    "Hi! Ask me to score parcels — e.g. “best solar sites near Mojave” or “data center sites in Bakersfield”. " +
    "I'll run the scoring and update the map and the results list.",
};

/**
 * Agent chat state + the mock turn loop (GEO-27).
 *
 * Until `/api/agent` is live (GEO-21), each turn drives the REAL pipeline: it resolves a Kern place
 * locally, sets the use case + drawn area on the store (so the map + ranked list update via
 * `useScoring`, the "tool calls"), and narrates from its OWN per-turn `/api/score` fetch. Narration
 * is deliberately DECOUPLED from the shared `scoreStatus` — each turn owns its bubble, so rapid
 * sends, a manual draw mid-turn, an error retry, or a same-area repeat can't orphan/mis-narrate
 * another turn's message. Only the streamed narration is mocked; the data is real. Graceful: a
 * scoring failure narrates and leaves the rest of the app working.
 */
export function useAgentChat() {
  const store = useMapStore();
  const [messages, setMessages] = useState<ChatMessage[]>([INTRO]);
  const idRef = useRef(0);
  const timersRef = useRef<Map<string, number>>(new Map());
  const mountedRef = useRef(true);

  const newId = () => `m${(idRef.current += 1)}`;
  const append = (m: ChatMessage) => setMessages((prev) => [...prev, m]);
  const patch = (id: string, p: Partial<ChatMessage>) =>
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...p } : m)));

  // Reveal text gradually for a streamed feel; instant under reduced-motion; cancels any in-flight
  // stream for the same message id.
  const streamInto = useCallback((id: string, full: string, refs?: ParcelRef[]) => {
    const existing = timersRef.current.get(id);
    if (existing) window.clearInterval(existing);
    const reduce =
      typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      patch(id, { text: full, streaming: false, refs });
      return;
    }
    patch(id, { text: "", streaming: true, refs: undefined });
    const step = Math.max(2, Math.ceil(full.length / 24));
    let i = 0;
    const tick = window.setInterval(() => {
      i = Math.min(full.length, i + step);
      patch(id, { text: full.slice(0, i) });
      if (i >= full.length) {
        window.clearInterval(tick);
        timersRef.current.delete(id);
        patch(id, { streaming: false, refs });
      }
    }, 28);
    timersRef.current.set(id, tick);
  }, []);

  const narrateScored = useCallback(
    (asstId: string, label: string, fc: ScoreFeatureCollection | null) => {
      const feats = fc?.features ?? [];
      if (feats.length === 0) {
        streamInto(
          asstId,
          `No parcels in ${label} passed the screen. Try a larger area, a different use case, or relaxed thresholds.`,
        );
        return;
      }
      const top = feats.slice(0, 3);
      const refs: ParcelRef[] = top.map((f) => ({
        id: f.properties.id,
        apn: f.properties.apn,
        score: f.properties.score,
        centroid: f.properties.centroid,
      }));
      const best = top[0].properties;
      streamInto(
        asstId,
        `Scored ${feats.length} parcel${feats.length === 1 ? "" : "s"} in ${label}. ` +
          `The strongest is ${best.apn ?? `parcel ${best.id}`} at ${best.score.toFixed(0)}/100. ` +
          "Tap a parcel below to see its breakdown.",
        refs,
      );
    },
    [streamInto],
  );

  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text) return;
      append({ id: newId(), role: "user", text });
      const asstId = newId();
      append({ id: asstId, role: "assistant", text: "", streaming: true });

      const parsed = parseRequest(text);

      if (parsed.wantsContext && !parsed.place) {
        try {
          const ctx = await apiClient.context();
          if (mountedRef.current) streamInto(asstId, narrateContext(ctx));
        } catch {
          if (mountedRef.current) {
            streamInto(asstId, "I couldn't load the grid context right now, but scoring and the map still work.");
          }
        }
        return;
      }

      const geometry = parsed.geometry ?? store.drawnPolygon ?? undefined;
      const label = parsed.label ?? "your drawn area";
      if (!geometry) {
        streamInto(
          asstId,
          "Tell me a Kern city (e.g. “Mojave”, “Bakersfield”, “Tehachapi”) or draw an area on the map — and " +
            "say “solar” or “data center”. For example: “best solar sites near Mojave”.",
        );
        return;
      }

      const uc = parsed.useCase ?? store.useCase;
      streamInto(
        asstId,
        `Resolving ${label} and scoring for ${uc === "data_center" ? "data centers" : "utility solar"}…`,
      );
      // The "tool calls": drive the real pipeline so the map + ranked list update (useScoring).
      store.setUseCase(uc);
      store.setDrawnPolygon(geometry);
      // Narrate from this turn's OWN fetch — decoupled from the shared scoreStatus, so this bubble
      // always resolves (server LRU-caches the identical request useScoring also issues).
      try {
        const fc = await apiClient.score({ geometry, use_case: uc, limit: SCORE_LIMIT });
        if (mountedRef.current) narrateScored(asstId, label, fc);
      } catch (err) {
        if (!mountedRef.current) return;
        const why = err instanceof ApiError ? err.message : "the scoring service is unavailable";
        streamInto(
          asstId,
          `I couldn't score ${label} (${why}). You can still draw on the map and explore — ` +
            "the live agent endpoint /api/agent arrives in GEO-21.",
        );
      }
    },
    [store, streamInto, narrateScored],
  );

  useEffect(() => {
    // Set true on EACH mount: under StrictMode (and any real remount, e.g. crossing the
    // mobile/desktop breakpoint) the cleanup below runs and would otherwise leave this false,
    // permanently skipping post-await narration.
    mountedRef.current = true;
    const timers = timersRef.current;
    return () => {
      mountedRef.current = false;
      timers.forEach((t) => window.clearInterval(t));
      timers.clear();
    };
  }, []);

  return { messages, send };
}
