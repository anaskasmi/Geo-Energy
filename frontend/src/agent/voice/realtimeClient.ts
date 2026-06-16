import { API_BASE_URL } from "../../config/env";

/**
 * Realtime session bootstrap (GEO-40). Asks our backend (`POST /api/realtime/session`) to mint a
 * short-lived OpenAI ephemeral client secret — the real OPENAI_API_KEY stays server-side. The
 * browser then uses `value` as the bearer when POSTing its WebRTC SDP offer straight to OpenAI.
 *
 * Voice mode is optional: when no key is configured the backend replies `{ configured: false }`, so
 * the UI can show a tidy disabled state rather than an error.
 */

export interface RealtimeSession {
  configured: boolean;
  value?: string; // ephemeral client secret (ek_/sk_realtime_…)
  model?: string;
  voice?: string;
  error?: string;
}

export const OPENAI_REALTIME_CALLS_URL = "https://api.openai.com/v1/realtime/calls";

export async function fetchRealtimeSession(signal?: AbortSignal): Promise<RealtimeSession> {
  const base = API_BASE_URL.replace(/\/+$/, "");
  let res: Response;
  try {
    res = await fetch(`${base}/realtime/session`, {
      method: "POST",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch {
    return { configured: true, error: "Couldn't reach the server to start voice mode." };
  }
  if (!res.ok) {
    return { configured: true, error: `Voice service unavailable (${res.status}).` };
  }
  try {
    return (await res.json()) as RealtimeSession;
  } catch {
    return { configured: true, error: "Unexpected response starting voice mode." };
  }
}
