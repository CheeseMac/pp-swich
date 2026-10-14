/** Shared types for the pp switch collector extension (FEAT-01). */

/** One sanitized observation ready to upload. Zero data values by
 *  construction: bodies are zero-value skeletons, query carries names
 *  only, headers are never included. */
export interface Observation {
  /** origin + coarsened path template; no query string */
  url: string;
  method: string;
  status: number;
  /** query parameter NAMES mapped to "" (zero value) */
  query: Record<string, string>;
  /** zero-value skeleton of the JSON request body, or null */
  request_body: unknown;
  /** zero-value skeleton of the JSON response body, or null */
  response_body: unknown;
  /** GraphQL operationName (API structure, not data) or null */
  graphql_operation: string | null;
  /** epoch ms, metadata only */
  observed_at: number;
}

export interface CandidatePreview {
  key: string;
  method: string;
  template: string;
  queryNames: string[];
  responseTopKeys: string[];
  statusCodes: number[];
  count: number;
}

export interface PairingInfo {
  serverUrl: string;
  serviceId: string;
  tokenExpiresAt: string;
}

export interface LastSubmit {
  at: number;
  uploaded: number;
  candidates: number;
  operationsCreated: number;
  operationsUpdated: number;
  error?: string;
}

export interface CaptureSession {
  tabId: number;
  origin: string;
  attached: boolean;
  startedAt: number;
}

export interface CollectorState {
  serverUrl: string;
  pairing: PairingInfo | null;
  session: CaptureSession | null;
  observations: Observation[];
  lastSubmit: LastSubmit | null;
}

export interface StateResponse {
  state: CollectorState;
  /** collected while the debugger was attached, kept across detach */
  detachedWarning: boolean;
}
