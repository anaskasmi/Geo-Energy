import { useCallback, useEffect, useRef, useState } from "react";

import type { ScoreFeatureCollection } from "../api/client";
import { generateParcelsReport } from "../export/parcelReport";
import { useMapStore } from "../map/useMapStore";
import { streamAgent } from "./agentClient";
import type { ChatMessage, ParcelRef } from "./types";

const INTRO: ChatMessage = {
  id: "intro",
  role: "assistant",
  text:
    "Hi! Ask me to score parcels — e.g. “best solar sites near Mojave” or “data center sites in Bakersfield”. " +
    "I'll run the scoring and update the map and the results list.",
};

/**
 * Agent chat state + the LIVE turn loop (GEO-21).
 *
 * Each turn POSTs the user's message to the real `/api/agent` SSE endpoint (Gemini via Pydantic AI)
 * and renders the stream as it arrives: `step` events show the current tool phase, `token` events
 * stream the narrative into the assistant bubble, and the `result` event's ranked FeatureCollection
 * is pushed straight onto the shared map store — so the agent's tool calls drive the REAL map +
 * results list. One turn at a time (`busy`); a fresh send aborts any in-flight stream. Self-healing:
 * transport/HTTP/model failures arrive as a clean `error` message instead of crashing the panel.
 */
export function useAgentChat() {
  const store = useMapStore();
  const [messages, setMessages] = useState<ChatMessage[]>([INTRO]);
  const [busy, setBusy] = useState(false);
  const idRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  const newId = () => `m${(idRef.current += 1)}`;
  const append = useCallback((m: ChatMessage) => setMessages((prev) => [...prev, m]), []);
  const patch = useCallback(
    (id: string, p: Partial<ChatMessage> | ((m: ChatMessage) => Partial<ChatMessage>)) =>
      setMessages((prev) =>
        prev.map((m) => (m.id === id ? { ...m, ...(typeof p === "function" ? p(m) : p) } : m)),
      ),
    [],
  );

  // Push the agent's ranked result onto the shared store — this is what makes the map's scored
  // overlay + the results list update — and surface the top parcels as clickable chips.
  const applyResult = useCallback(
    (asstId: string, fc: ScoreFeatureCollection) => {
      store.setScoreResult(fc);
      store.setScoreStatus("done");
      const feats = fc.features ?? [];
      // Focus the map on the scored area: fly to the mean of the returned parcel centroids.
      const cents = feats
        .map((f) => f.properties.centroid)
        .filter((c): c is [number, number] => Array.isArray(c));
      if (cents.length > 0) {
        const avg: [number, number] = [
          cents.reduce((s, c) => s + c[0], 0) / cents.length,
          cents.reduce((s, c) => s + c[1], 0) / cents.length,
        ];
        store.flyTo(avg, 10);
      }
      const refs: ParcelRef[] = feats.slice(0, 3).map((f) => ({
        id: f.properties.id,
        apn: f.properties.apn,
        score: f.properties.score,
        centroid: f.properties.centroid,
      }));
      patch(asstId, { refs });
    },
    [store, patch],
  );

  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || busy) return; // one turn at a time

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setBusy(true);

      append({ id: newId(), role: "user", text });
      const asstId = newId();
      append({ id: asstId, role: "assistant", text: "", streaming: true });

      // Forward the currently drawn area so the agent can score / check affordability on the
      // user's actual selection (not just a place name re-parsed from the text).
      const areaGeometry = store.drawnPolygon;
      await streamAgent(
        text,
        {
          onStep: (phase) => {
            if (mountedRef.current) patch(asstId, { phase });
          },
          onToken: (delta) => {
            if (mountedRef.current) {
              // First token clears the phase line; subsequent tokens append to the bubble.
              patch(asstId, (m) => ({ text: m.text + delta, phase: undefined, streaming: true }));
            }
          },
          onResult: (fc) => {
            if (mountedRef.current) applyResult(asstId, fc);
          },
          onAffordability: (affordability) => {
            if (mountedRef.current) patch(asstId, { affordability });
          },
          onFocus: (focus) => {
            if (!mountedRef.current) return;
            // Select + zoom to the parcel the agent named (parcel-level zoom).
            store.setSelected({ id: focus.parcel_id, apn: focus.apn, acres: null });
            store.flyTo(focus.centroid, 15);
          },
          onExportPdf: (req) => {
            if (!mountedRef.current) return;
            void generateParcelsReport({
              ids: req.parcel_ids,
              result: store.scoreResult,
              useCase: store.useCase,
              snapshot: store.captureMapSnapshot(),
            })
              .then((r) => {
                if (!mountedRef.current) return;
                if (r.count === 0) {
                  patch(asstId, (m) => ({
                    text: `${m.text}\n\n_(No scored parcels to export yet — score an area first.)_`,
                  }));
                } else if (r.capped) {
                  patch(asstId, (m) => ({
                    text: `${m.text}\n\n_(Exported the top ${r.count} of ${r.requested} parcels to PDF.)_`,
                  }));
                }
              })
              .catch(() => {
                if (mountedRef.current) {
                  patch(asstId, (m) => ({ text: `${m.text}\n\n_(Couldn't generate the PDF.)_` }));
                }
              });
          },
          onError: (message) => {
            if (mountedRef.current) {
              patch(asstId, (m) => ({
                text: m.text ? `${m.text}\n\n${message}` : message,
                phase: undefined,
              }));
            }
          },
        },
        controller.signal,
        areaGeometry,
      );

      if (mountedRef.current) {
        patch(asstId, (m) => ({
          streaming: false,
          phase: undefined,
          text: m.text || "I didn't get a response. Please try again.",
        }));
      }
      setBusy(false);
      abortRef.current = null;
    },
    [busy, append, patch, applyResult, store],
  );

  useEffect(() => {
    // Set true on EACH mount: under StrictMode (and any real remount, e.g. crossing the
    // mobile/desktop breakpoint) the cleanup below runs and would otherwise leave this false.
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  return { messages, send, busy };
}
