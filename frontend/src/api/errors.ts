import { ApiError } from "./client";

/**
 * Centralized failure → user-facing message mapping (GEO-32 #10).
 *
 * Every transient/deterministic failure in the app funnels through `describeError` so the UI
 * shows a SPECIFIC, actionable message + a recovery affordance instead of a raw stack/status.
 * `action` tells the UI which recovery control to render:
 *   - "retry"   → offer a Retry button (transient: network / 5xx / 429).
 *   - "smaller" → guidance to draw a smaller area (413 / 422 / client-side area guard).
 *   - "none"    → informational only (deterministic 4xx).
 */
export type ErrorAction = "retry" | "smaller" | "none";

export interface ErrorInfo {
  title: string;
  detail: string;
  action: ErrorAction;
}

/** True for failures worth retrying with backoff: network errors, 5xx, and 429 (rate limit). */
export function isTransient(err: unknown): boolean {
  if (err instanceof ApiError) return err.status === 429 || err.status >= 500;
  // A non-ApiError reaching here is a fetch/network failure (TypeError) — transient.
  return !(err instanceof DOMException && err.name === "AbortError");
}

/** Map any thrown value (ApiError or network error) to a titled, actionable message. */
export function describeError(err: unknown): ErrorInfo {
  if (err instanceof ApiError) {
    if (err.status === 429) {
      return {
        title: "Too many requests",
        detail: "The service is busy. Wait a moment, then try again.",
        action: "retry",
      };
    }
    if (err.status === 413 || err.status === 422) {
      return {
        title: "Search area is too large",
        detail: "The drawn area couldn't be scored. Draw a smaller area and try again.",
        action: "smaller",
      };
    }
    if (err.status >= 500) {
      return {
        title: "Scoring service problem",
        detail: "The scoring service had a problem. This is usually temporary — try again.",
        action: "retry",
      };
    }
    // Other deterministic 4xx: surface the server's message, no auto-recovery.
    return { title: "Couldn't score the area", detail: err.message, action: "none" };
  }
  // Non-ApiError → network/connection failure.
  return {
    title: "Can't reach the scoring service",
    detail: "Check your connection. The map and the rest of the app still work.",
    action: "retry",
  };
}
