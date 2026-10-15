/** Control-plane client for the collector surface (FEAT-01).
 *
 * The extension only ever calls:
 * - POST /api/v1/collector/pair          (one-time pairing code -> token)
 * - POST /api/v1/collector/observations  (bearer collector token)
 *
 * It NEVER holds or sees a user management token. The fetch
 * implementation is injectable so the runtime is unit-testable.
 */

import type { Observation } from "./types.js";

export interface PairResult {
  collector_token: string;
  service_id: string;
  token_expires_at: string;
}

export interface SubmitResult {
  job_id: string;
  state: string;
  candidates: number;
  operations_created: number;
  operations_updated: number;
  observations_created: number;
}

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

async function unwrap<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => ({}))) as {
    data?: T;
    error?: { code?: string; message?: string };
  };
  if (!response.ok) {
    throw new Error(
      payload.error?.message ?? `${payload.error?.code ?? "ERROR"} (HTTP ${response.status})`,
    );
  }
  if (payload.data === undefined) throw new Error("Malformed response envelope");
  return payload.data;
}

export async function pair(
  serverUrl: string,
  pairingCode: string,
  fetchImpl: FetchLike = (input, init) => fetch(input, init),
): Promise<PairResult> {
  const response = await fetchImpl(
    new URL("/api/v1/collector/pair", serverUrl).toString(),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pairing_code: pairingCode.trim().toUpperCase() }),
    },
  );
  return unwrap<PairResult>(response);
}

export async function submitObservations(
  serverUrl: string,
  collectorToken: string,
  observations: Observation[],
  fetchImpl: FetchLike = (input, init) => fetch(input, init),
): Promise<SubmitResult> {
  const response = await fetchImpl(
    new URL("/api/v1/collector/observations", serverUrl).toString(),
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${collectorToken}`,
      },
      body: JSON.stringify({ observations }),
    },
  );
  return unwrap<SubmitResult>(response);
}
