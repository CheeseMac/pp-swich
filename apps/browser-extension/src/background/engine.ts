/** CollectorEngine: CDP Network events in -> sanitized Observations out.
 *
 * Pure logic - no chrome.* access, fully unit-testable. The service
 * worker (cdp.ts/index.ts) feeds it events and body getters.
 *
 * Event lifecycle handled here:
 * - ``requestWillBeSent`` may fire multiple times per requestId during a
 *   redirect chain - only the LAST request (final destination) is kept;
 * - a requestId is processed exactly once (duplicate ``loadingFinished``
 *   events cannot double-count);
 * - ``loadingFailed`` drops the pending request;
 * - only Fetch/XHR resources produce observations;
 * - bodies are fetched immediately at ``loadingFinished`` (CDP evicts
 *   them soon after); failures degrade to a body-less observation.
 */

import { toSkeleton } from "../shared/skeleton.js";
import { coarsenPath, localKey, parseQueryNames, splitUrl } from "../shared/paths.js";
import type { CandidatePreview, Observation } from "../shared/types.js";

export interface EngineConfig {
  /** origins never collected (the pp-switch console itself) */
  excludedOrigins: Set<string>;
  maxBodyBytes: number;
  maxBuffer: number;
}

export const DEFAULT_CONFIG: EngineConfig = {
  excludedOrigins: new Set(),
  maxBodyBytes: 256 * 1024,
  maxBuffer: 5_000,
};

interface PendingRequest {
  url: string;
  method: string;
  postData: string | null;
  status: number | null;
  resourceType: string | null;
  mimeType: string | null;
}

export interface CdpBody {
  body: string;
  base64Encoded: boolean;
}

export type BodyFetcher = (requestId: string) => Promise<CdpBody | null>;

/** Internal merge state per candidate; preview() projects it to the UI shape. */
interface PreviewGroup {
  key: string;
  method: string;
  template: string;
  queryNames: string[];
  respKeys: Set<string>;
  codes: Set<number>;
  count: number;
}

const CAPTURED_RESOURCE_TYPES = new Set(["Fetch", "XHR"]);

export class CollectorEngine {
  private pending = new Map<string, PendingRequest>();
  private processed = new Set<string>();
  readonly observations: Observation[] = [];
  private groups = new Map<string, PreviewGroup>();
  private readonly config: EngineConfig;

  constructor(config: Partial<EngineConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /** CDP Network.requestWillBeSent */
  onRequestWillBeSent(
    requestId: string,
    params: {
      request?: { url?: string; method?: string; postData?: string | null };
      redirectResponse?: unknown;
      type?: string;
    },
  ): void {
    const request = params.request ?? {};
    const url = request.url ?? "";
    if (!url) return;
    // overwrite on redirect: the last requestWillBeSent for a requestId
    // is the final destination of the chain
    this.pending.set(requestId, {
      url,
      method: (request.method ?? "GET").toUpperCase(),
      postData: request.postData ?? null,
      status: null,
      resourceType: params.type ?? null,
      mimeType: null,
    });
  }

  /** CDP Network.responseReceived */
  onResponseReceived(
    requestId: string,
    params: { response?: { status?: number; mimeType?: string }; type?: string },
  ): void {
    const entry = this.pending.get(requestId);
    if (!entry) return;
    entry.status = params.response?.status ?? null;
    entry.mimeType = params.response?.mimeType ?? null;
    if (params.type) entry.resourceType = params.type;
  }

  /** CDP Network.loadingFailed: drop the pending request */
  onLoadingFailed(requestId: string): void {
    this.pending.delete(requestId);
  }

  /** CDP Network.loadingFinished: build + buffer the observation */
  async onLoadingFinished(
    requestId: string,
    getResponseBody: BodyFetcher,
    getPostData?: BodyFetcher,
    now: Date = new Date(),
  ): Promise<Observation | null> {
    if (this.processed.has(requestId)) return null; // no double counting
    const entry = this.pending.get(requestId);
    this.pending.delete(requestId);
    if (!entry) return null;
    this.processed.add(requestId);

    if (entry.resourceType && !CAPTURED_RESOURCE_TYPES.has(entry.resourceType)) {
      return null;
    }

    const parts = splitUrl(entry.url);
    if (!parts) return null;
    if (this.config.excludedOrigins.has(parts.origin)) return null;

    const template = coarsenPath(parts.pathname);
    const queryNames = parseQueryNames(parts.search);

    const requestBody = this.bodySkeleton(
      entry.postData,
      async () =>
        getPostData ? getPostData(requestId) : null,
      entry.method,
    );
    let requestSkeleton: unknown = null;
    let graphqlOperation: string | null = null;
    const rawRequest = await requestBody;
    if (rawRequest !== null) {
      requestSkeleton = rawRequest.skeleton;
      graphqlOperation = rawRequest.graphqlOperation;
    }

    let responseSkeleton: unknown = null;
    const status = entry.status ?? 0;
    const cdpBody = await getResponseBody(requestId).catch(() => null);
    if (cdpBody) {
      let text: string | null = null;
      if (!cdpBody.base64Encoded) {
        text = cdpBody.body;
      } else if (this.isJsonMime(entry.mimeType)) {
        try {
          text = atob(cdpBody.body);
        } catch {
          text = null;
        }
      }
      const parsed = text !== null ? this.parseJson(text) : null;
      if (parsed !== null) responseSkeleton = toSkeleton(parsed);
    }

    const observation: Observation = {
      url: parts.origin + template,
      method: entry.method,
      status,
      query: Object.fromEntries(queryNames.map((name) => [name, ""])),
      request_body: requestSkeleton,
      response_body: responseSkeleton,
      graphql_operation: graphqlOperation,
      observed_at: now.getTime(),
    };
    this.push(observation, queryNames);
    return observation;
  }

  /** Drop observations already uploaded (keep the last ``keep`` on error). */
  dropUploaded(count: number): void {
    this.observations.splice(0, count);
    this.rebuildGroups();
  }

  clear(): void {
    this.observations.length = 0;
    this.pending.clear();
    this.processed.clear();
    this.rebuildGroups();
  }

  preview(): CandidatePreview[] {
    return [...this.groups.values()]
      .map((group: PreviewGroup): CandidatePreview => ({
        key: group.key,
        method: group.method,
        template: group.template,
        queryNames: [...group.queryNames].sort(),
        responseTopKeys: [...group.respKeys].sort().slice(0, 12),
        statusCodes: [...group.codes].sort((a, b) => a - b),
        count: group.count,
      }))
      .sort((a, b) => b.count - a.count || a.template.localeCompare(b.template));
  }

  get pendingCount(): number {
    return this.pending.size;
  }

  // ------------------------------------------------------------------

  private isJsonMime(mime: string | null): boolean {
    return !!mime && mime.toLowerCase().includes("json");
  }

  private parseJson(text: string): unknown | null {
    const trimmed = text.trim();
    if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null;
    try {
      return JSON.parse(trimmed) as unknown;
    } catch {
      return null;
    }
  }

  private async bodySkeleton(
    postData: string | null,
    fetchMissing: () => Promise<CdpBody | null>,
    method: string,
  ): Promise<{ skeleton: unknown; graphqlOperation: string | null } | null> {
    let text = postData;
    if (text === null) {
      const cdpBody = await fetchMissing().catch(() => null);
      if (!cdpBody || cdpBody.base64Encoded) return null;
      text = cdpBody.body;
    }
    if (!text || text.length > this.config.maxBodyBytes) return null;
    const parsed = this.parseJson(text);
    if (parsed === null) return null;

    let graphqlOperation: string | null = null;
    if (method === "POST" && typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      const name = (parsed as Record<string, unknown>)["operationName"];
      if (typeof name === "string" && name) graphqlOperation = name;
    }
    return { skeleton: toSkeleton(parsed), graphqlOperation };
  }

  private push(observation: Observation, queryNames: string[]): void {
    if (this.observations.length >= this.config.maxBuffer) {
      this.observations.shift(); // bounded memory
    }
    this.observations.push(observation);
    this.addToPreview(observation, queryNames);
  }

  private addToPreview(observation: Observation, queryNames: string[]): void {
    const pathname = observation.url.replace(/^[a-z]+:\/\/[^/]+/i, "");
    const key = localKey(observation.method, pathname, queryNames);
    const group = this.groups.get(key) ?? this.newGroup(key, observation.method, pathname);
    this.groups.set(key, group);
    group.count++;
    for (const name of queryNames) {
      if (!group.queryNames.includes(name)) group.queryNames.push(name);
    }
    group.codes.add(observation.status);
    const body = observation.response_body;
    if (body !== null && typeof body === "object" && !Array.isArray(body)) {
      for (const k of Object.keys(body as Record<string, unknown>)) {
        group.respKeys.add(k);
      }
    }
  }

  private newGroup(key: string, method: string, template: string): PreviewGroup {
    return {
      key,
      method,
      template,
      queryNames: [],
      respKeys: new Set<string>(),
      codes: new Set<number>(),
      count: 0,
    };
  }

  private rebuildGroups(): void {
    this.groups.clear();
    for (const observation of this.observations) {
      this.addToPreview(observation, Object.keys(observation.query));
    }
  }
}
