/**
 * Tiny haptic-feedback helper (GEO-29). A no-op where the Vibration API is unavailable
 * (desktop, iOS Safari) or blocked — never throws. Use sparingly: a short pulse to confirm a
 * discrete touch action (start drawing, select a result, long-press).
 */
export function haptic(ms = 10): void {
  try {
    navigator.vibrate?.(ms);
  } catch {
    /* unsupported / disallowed — ignore */
  }
}
