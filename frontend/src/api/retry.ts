import { isTransient } from "./errors";

/**
 * Bounded exponential backoff with jitter for transient failures (GEO-32 #11).
 *
 * Wraps an abortable request factory and retries ONLY transient errors (network / 5xx / 429 —
 * see `isTransient`); deterministic 4xx (e.g. 422 bad polygon) fail fast with no retry. The
 * AbortSignal is honored throughout: an abort rejects immediately and cancels the backoff wait,
 * so a superseded request never lingers. Reusable across the scoring / explain / context paths.
 */
export interface RetryOptions {
  /** Total attempts (including the first). Default 3. */
  attempts?: number;
  /** Base backoff in ms (doubled each retry, plus jitter). Default 400. */
  baseMs?: number;
  /** Cap on a single backoff wait. Default 4000. */
  maxMs?: number;
  signal?: AbortSignal;
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function withRetry<T>(
  factory: (signal?: AbortSignal) => Promise<T>,
  options: RetryOptions = {},
): Promise<T> {
  const { attempts = 3, baseMs = 400, maxMs = 4000, signal } = options;
  let lastErr: unknown;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    try {
      return await factory(signal);
    } catch (err) {
      lastErr = err;
      // Never retry an abort or a deterministic failure; stop on the final attempt.
      if (signal?.aborted) throw err;
      if (!isTransient(err) || attempt === attempts - 1) throw err;
      const backoff = Math.min(maxMs, baseMs * 2 ** attempt);
      const jittered = backoff / 2 + Math.random() * (backoff / 2);
      await wait(jittered, signal);
    }
  }
  throw lastErr;
}
