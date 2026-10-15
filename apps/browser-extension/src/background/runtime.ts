/** CollectorRuntime: survives service-worker suspension and restart.
 *
 * FEAT-01-F1 reliability contract:
 * - the collector token lives in chrome.storage.session under its own key
 *   (never inside CollectorState, never returned to the popup);
 * - every completed observation is persisted to storage BEFORE the
 *   auto-flush decision, so a suspended SW never loses buffered work and
 *   the threshold check always sees fresh counts;
 * - `restore()` revalidates the token on SW startup and clears the
 *   pairing when it expired while the SW was down;
 * - `submit()` uploads exactly the buffer it read and drops exactly what
 *   was uploaded - no loss, no double upload across SW restarts.
 *
 * Pure orchestration over injectable pieces: fully unit-testable without
 * chrome.* (see tests/runtime.test.ts).
 */

import { CollectorEngine, type EngineConfig } from "./engine.js";
import type { StateStore } from "./store.js";
import { pair as apiPair, submitObservations } from "../shared/api.js";
import type { CollectorState, Observation } from "../shared/types.js";

const TOKEN_KEY = "collectorToken";
export const AUTO_FLUSH_THRESHOLD = 50;
export const SUBMIT_BATCH = 200; // server cap (PP_SWITCH_COLLECTOR_MAX_OBSERVATIONS)

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export class CollectorRuntime {
  private engine = new CollectorEngine();
  private token: string | null = null;
  readonly store: StateStore;
  private readonly fetchImpl: FetchLike;
  private flushing = false;

  constructor(opts: { store: StateStore; fetchImpl?: FetchLike }) {
    this.store = opts.store;
    this.fetchImpl = opts.fetchImpl ?? ((input, init) => fetch(input, init));
  }

  get engineRef(): CollectorEngine {
    return this.engine;
  }

  /** SW startup: rebuild the engine buffer, revalidate the token. */
  async restore(): Promise<void> {
    const state = await this.store.get();
    const config: Partial<EngineConfig> = {};
    try {
      config.excludedOrigins = new Set([new URL(state.serverUrl).origin]);
      if (state.session?.origin) {
        config.allowedOrigins = new Set([state.session.origin]);
      }
    } catch {
      config.excludedOrigins = new Set();
    }
    this.engine = new CollectorEngine(config);
    this.engine.observations.push(...state.observations);

    // the chrome.debugger attachment cannot survive SW termination -
    // restore() always reopens in the detached state (buffer intact)
    let stateChanged = false;
    if (state.session?.attached) {
      state.session.attached = false;
      stateChanged = true;
    }

    const tokenBag = await this.store.rawGet(TOKEN_KEY);
    this.token = (tokenBag[TOKEN_KEY] as string | undefined) ?? null;
    const expires = state.pairing ? Date.parse(state.pairing.tokenExpiresAt) : NaN;
    const expired =
      this.token !== null && state.pairing !== null && Number.isFinite(expires)
        ? expires <= Date.now()
        : false;
    if (expired) this.token = null;
    if (this.token === null) {
      await this.store.rawRemove(TOKEN_KEY);
      if (state.pairing !== null) {
        // token gone/expired while the SW was down: stop claiming
        // "connected" in the popup
        state.pairing = null;
        stateChanged = true;
      }
    }
    if (stateChanged) {
      state.observations = [...this.engine.observations];
      await this.store.set(state);
    }
  }

  async getState(): Promise<CollectorState> {
    return this.persist();
  }

  /** Persist the live engine buffer into the stored state. */
  async persist(): Promise<CollectorState> {
    const state = await this.store.get();
    state.observations = [...this.engine.observations];
    await this.store.set(state);
    return state;
  }

  async setServerUrl(url: string): Promise<CollectorState> {
    const state = await this.store.get();
    const normalized = url.trim().replace(/\/+$/, "");
    if (!/^https?:\/\//.test(normalized)) {
      throw new Error("Server URL must start with http:// or https://");
    }
    state.serverUrl = normalized;
    await this.store.set(state);
    return state;
  }

  async pair(pairingCode: string): Promise<CollectorState> {
    const state = await this.store.get();
    const result = await apiPair(state.serverUrl, pairingCode, this.fetchImpl);
    this.token = result.collector_token;
    await this.store.rawSet(TOKEN_KEY, result.collector_token);
    state.pairing = {
      serverUrl: state.serverUrl,
      serviceId: result.service_id,
      tokenExpiresAt: result.token_expires_at,
    };
    await this.store.set(state);
    return state;
  }

  async unpair(): Promise<CollectorState> {
    this.token = null;
    await this.store.rawRemove(TOKEN_KEY);
    const state = await this.store.get();
    state.pairing = null;
    await this.store.set(state);
    return state;
  }

  /** Attach the engine to one tab; collection is scoped to that origin. */
  async startCapture(tabId: number, origin: string): Promise<CollectorState> {
    const state = await this.store.get();
    const config: Partial<EngineConfig> = {};
    try {
      config.excludedOrigins = new Set([new URL(state.serverUrl).origin]);
    } catch {
      throw new Error("Invalid server URL");
    }
    // domain scope: only the attached tab's own origin is collected by
    // default - third-party Fetch/XHR observed on the page is dropped
    config.allowedOrigins = new Set([origin]);
    this.engine = new CollectorEngine(config);
    this.engine.observations.push(...state.observations);
    state.session = { tabId, origin, attached: true, startedAt: Date.now() };
    await this.store.set(state);
    return state;
  }

  /** Debugger banner cancelled / tab closed: session ends, buffer kept. */
  async noteAttachLost(): Promise<void> {
    const state = await this.store.get();
    if (state.session) state.session.attached = false;
    state.observations = [...this.engine.observations];
    await this.store.set(state);
  }

  async stopCapture(): Promise<CollectorState> {
    const state = await this.store.get();
    state.session = null;
    state.observations = [...this.engine.observations];
    await this.store.set(state);
    return state;
  }

  /** After EVERY completed observation: persist first, then maybe flush. */
  async onObservationComplete(): Promise<void> {
    await this.persist();
    await this.autoFlush();
  }

  private async autoFlush(): Promise<void> {
    if (this.token === null || this.flushing) return;
    const state = await this.store.get();
    if (!state.pairing || state.observations.length < AUTO_FLUSH_THRESHOLD) return;
    this.flushing = true;
    try {
      await this.submit();
    } catch {
      // surfaced by manual Submit; buffer stays intact
    } finally {
      this.flushing = false;
    }
  }

  async submit(): Promise<CollectorState> {
    if (this.token === null) throw new Error("Not paired with pp switch yet");
    const state = await this.store.get();
    if (!state.pairing) throw new Error("Not paired with pp switch yet");
    const buffered = [...this.engine.observations];
    if (buffered.length === 0) throw new Error("Nothing to submit yet");

    let uploadedTotal = 0;
    let last;
    while (uploadedTotal < buffered.length) {
      const batch: Observation[] = buffered.slice(
        uploadedTotal,
        uploadedTotal + SUBMIT_BATCH,
      );
      last = await submitObservations(
        state.serverUrl,
        this.token,
        batch,
        this.fetchImpl,
      );
      uploadedTotal += batch.length;
    }
    // drop exactly what was uploaded, from the live engine buffer
    this.engine.dropUploaded(buffered.length);
    state.observations = [...this.engine.observations];
    state.lastSubmit = {
      at: Date.now(),
      uploaded: buffered.length,
      candidates: last?.candidates ?? 0,
      operationsCreated: last?.operations_created ?? 0,
      operationsUpdated: last?.operations_updated ?? 0,
    };
    await this.store.set(state);
    return state;
  }

  async clearBuffer(): Promise<CollectorState> {
    this.engine.clear();
    return this.persist();
  }
}
