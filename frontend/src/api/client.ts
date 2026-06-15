import { API_BASE_URL } from "../config/env";

/**
 * Minimal API client for the Site-Selection API.
 *
 * Reads the base URL from `import.meta.env.VITE_API_BASE_URL` (via config/env) and
 * exposes typed request helpers. No real endpoints exist yet (scoring is GEO-16+), so
 * this is just the base-url plumbing plus a `health` probe (GEO-15 adds /api/health).
 */

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function buildUrl(path: string): string {
  const base = API_BASE_URL.replace(/\/+$/, "");
  const suffix = path.replace(/^\/+/, "");
  return `${base}/${suffix}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildUrl(path), {
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    throw new ApiError(response.status, `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export interface HealthResponse {
  status: string;
  [key: string]: unknown;
}

export const apiClient = {
  baseUrl: API_BASE_URL,
  /** Liveness/readiness probe (GEO-15 exposes /api/health). */
  health: () => request<HealthResponse>("health"),
};

export type ApiClient = typeof apiClient;
