import type { GeoJsonGeometry, UseCase } from "../api/client";

export type ChatRole = "user" | "assistant";

/** A clickable reference to a scored parcel, surfaced under an assistant message (GEO-27). */
export interface ParcelRef {
  id: number | string;
  apn: string | null;
  score: number;
  centroid: [number, number] | null;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  /** Clickable parcel references (rendered as chips below the bubble). */
  refs?: ParcelRef[];
  /** True while the text is still streaming in. */
  streaming?: boolean;
}

/** Parsed intent from a free-text request (the mock's stand-in for the agent's planning). */
export interface ParsedRequest {
  place?: string;
  label?: string;
  geometry?: GeoJsonGeometry;
  useCase?: UseCase;
  wantsContext: boolean;
}

/**
 * The streamed event contract the LIVE `/api/agent` (GEO-21 — Gemini via Pydantic AI) will emit
 * over SSE. The mock UI drives the real scoring pipeline instead of emitting these, but the chat
 * is shaped so swapping to a live fetch-stream that yields these events is a contained change.
 */
export type AgentStreamEvent =
  | { type: "text-delta"; delta: string }
  | { type: "tool-call"; name: string; args: Record<string, unknown> }
  | { type: "tool-result"; name: string; ok: boolean }
  | { type: "feature_collection" }
  | { type: "error"; message: string }
  | { type: "done" };
