import type { GeoJsonGeometry, ScoreFeatureCollection } from "../api/client";
import { API_BASE_URL } from "../config/env";
import type {
  Affordability,
  AgentStreamEvent,
  ExportPdfRequest,
  FocusParcel,
  MapViewRequest,
  ZoomMapRequest,
} from "./types";

/**
 * Live agent SSE client (GEO-21) — talks to the real `POST /api/agent` (Gemini via Pydantic AI,
 * api/app/agent.py). The backend orchestrates the local geospatial tools server-side and streams
 * Server-Sent Events: `step` (a tool call started), `token` (a narrative chunk), `result` (the
 * ranked FeatureCollection from score_parcels), `error`, and `done`. We parse the byte stream into
 * typed events and dispatch them through handler callbacks. Never throws — transport/HTTP failures
 * are surfaced through `onError` so the chat degrades gracefully instead of crashing the panel.
 */

/** Suggested prompt chips shown under the chat input. */
export const SUGGESTIONS = [
  "Best solar sites near Mojave",
  "Data center sites in Bakersfield",
  "Grid queue context",
];

/** Friendly label for each tool phase the agent emits via `step` events (api/app/agent.py `_PHASE`). */
export const PHASE_LABELS: Record<string, string> = {
  resolving_area: "Resolving area…",
  scoring: "Scoring parcels…",
  explaining: "Analyzing parcel…",
  grid_context: "Checking the grid queue…",
  checking_affordability: "Checking land affordability…",
  focusing_parcel: "Zooming to parcel…",
  exporting_pdf: "Preparing PDF…",
  updating_map: "Updating the map…",
  zooming: "Zooming the map…",
};

export interface AgentHandlers {
  onStep?: (phase: string, tool: string) => void;
  onToken?: (text: string) => void;
  onResult?: (fc: ScoreFeatureCollection, area?: string) => void;
  onAffordability?: (affordability: Affordability) => void;
  onFocus?: (focus: FocusParcel) => void;
  onExportPdf?: (request: ExportPdfRequest) => void;
  onMapView?: (request: MapViewRequest) => void;
  onZoomMap?: (request: ZoomMapRequest) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

function agentUrl(): string {
  return `${API_BASE_URL.replace(/\/+$/, "")}/agent`;
}

/** Parse one SSE frame ("event: <type>\ndata: <json>") into a typed event, or null if unparseable. */
function parseFrame(frame: string): AgentStreamEvent | null {
  let event = "";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (!event) return null;
  let data: Record<string, unknown> = {};
  const raw = dataLines.join("\n");
  if (raw) {
    try {
      data = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return null; // skip a malformed frame rather than break the whole stream
    }
  }
  switch (event) {
    case "step":
      return { type: "step", phase: String(data.phase ?? ""), tool: String(data.tool ?? "") };
    case "token":
      return { type: "token", text: String(data.text ?? "") };
    case "result":
      return {
        type: "result",
        featureCollection: data.featureCollection as ScoreFeatureCollection | undefined,
        area: typeof data.area === "string" ? data.area : undefined,
        affordability: (data.affordability as Affordability | undefined) ?? undefined,
        focus: (data.focus as FocusParcel | undefined) ?? undefined,
        exportPdf: (data.exportPdf as ExportPdfRequest | undefined) ?? undefined,
        mapView: (data.mapView as MapViewRequest | undefined) ?? undefined,
        zoomMap: (data.zoomMap as ZoomMapRequest | undefined) ?? undefined,
      };
    case "error":
      return { type: "error", message: String(data.message ?? "the assistant hit an error") };
    case "done":
      return { type: "done" };
    default:
      return null;
  }
}

/**
 * POST a message to `/api/agent` and dispatch each streamed event through `handlers`.
 * Resolves when the stream ends (`done`), the body closes, or `signal` aborts. A new send should
 * abort the previous controller; aborts resolve silently (no `onError`).
 */
export async function streamAgent(
  message: string,
  handlers: AgentHandlers,
  signal?: AbortSignal,
  areaGeometry?: GeoJsonGeometry | null,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(agentUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      // Forward the user's drawn area (if any) so the agent operates on the SELECTED area; the
      // backend resolves it to an opaque token (the model never sees coordinates).
      body: JSON.stringify(areaGeometry ? { message, area_geometry: areaGeometry } : { message }),
      signal,
    });
  } catch {
    if (signal?.aborted) return;
    handlers.onError?.("I couldn't reach the assistant. Check that the API is running.");
    return;
  }

  if (!response.ok || !response.body) {
    // 422 (message too long), 503 (no artifact), etc. — surface a clean detail, never a stack.
    let detail = `the assistant is unavailable (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // non-JSON error body — keep the status-derived message
    }
    handlers.onError?.(detail);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      // Frames are separated by a blank line (\n\n); process every complete frame in the buffer.
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const ev = parseFrame(frame);
        if (!ev) continue;
        switch (ev.type) {
          case "step":
            handlers.onStep?.(ev.phase, ev.tool);
            break;
          case "token":
            handlers.onToken?.(ev.text);
            break;
          case "result":
            if (ev.featureCollection) handlers.onResult?.(ev.featureCollection, ev.area);
            if (ev.affordability) handlers.onAffordability?.(ev.affordability);
            // focus AFTER result so a "zoom to parcel" flyTo wins over the result's fit-to-area.
            if (ev.focus) handlers.onFocus?.(ev.focus);
            if (ev.exportPdf) handlers.onExportPdf?.(ev.exportPdf);
            if (ev.mapView) handlers.onMapView?.(ev.mapView);
            if (ev.zoomMap) handlers.onZoomMap?.(ev.zoomMap);
            break;
          case "error":
            handlers.onError?.(ev.message);
            break;
          case "done":
            handlers.onDone?.();
            return;
        }
      }
    }
  } catch {
    if (signal?.aborted) return;
    handlers.onError?.("The connection to the assistant was interrupted.");
  }
}
