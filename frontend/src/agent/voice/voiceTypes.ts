/**
 * Voice-mode types (GEO-40). The assistant's voice mode is an OpenAI Realtime session opened over
 * WebRTC directly from the browser (audio never touches our server). These types describe the small
 * surface the UI cares about: the session lifecycle state, live transcripts, and the contract for a
 * function/tool the model can call to drive the map.
 */

export type VoiceState = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "error";

export interface VoiceTranscript {
  id: string;
  role: "user" | "assistant";
  text: string;
}

/** A function the realtime model may call. `execute` runs locally (drives the map) and returns a
 *  small JSON-able summary that is sent back to the model so it can speak the "so what". */
export interface VoiceTool {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  execute: (args: Record<string, unknown>) => Promise<unknown>;
}

export interface UseVoiceModeOptions {
  /** Spoken-assistant persona + rules, injected as the realtime session instructions. */
  instructions: string;
  /** Tools the model can call to act on the app. */
  tools: VoiceTool[];
}
