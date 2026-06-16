import type { ScoreFeatureCollection } from "../api/client";

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
  /** The current tool phase (a `step` event), shown while the agent works; cleared once text/result arrives. */
  phase?: string;
}

/**
 * The streamed event contract the LIVE `/api/agent` (GEO-21 — Gemini via Pydantic AI) emits over
 * SSE. Mirrors the server frames in api/app/agent.py: `step | token | result | error | done`.
 */
export type AgentStreamEvent =
  | { type: "step"; phase: string; tool: string }
  | { type: "token"; text: string }
  | { type: "result"; featureCollection: ScoreFeatureCollection; area?: string }
  | { type: "error"; message: string }
  | { type: "done" };
