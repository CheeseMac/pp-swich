/** Collector session state persistence.
 *
 * chrome.storage.session keeps the buffer, pairing and collector token
 * across service-worker suspensions; it is cleared when the browser
 * closes, so an unpaired collector leaves nothing on disk. The token
 * lives under its OWN key (never inside CollectorState) so state
 * messages to the popup can never carry it.
 */

import type { CollectorState } from "../shared/types.js";

export const EMPTY_STATE: CollectorState = {
  serverUrl: "http://127.0.0.1:8911",
  pairing: null,
  session: null,
  observations: [],
  lastSubmit: null,
};

export interface StateStore {
  get(): Promise<CollectorState>;
  set(state: CollectorState): Promise<void>;
  /** raw key-value access beside the state object (token storage) */
  rawGet(key: string): Promise<Record<string, unknown>>;
  rawSet(key: string, value: unknown): Promise<void>;
  rawRemove(key: string): Promise<void>;
}

export class SessionStore implements StateStore {
  private static key = "collectorState";

  async get(): Promise<CollectorState> {
    const bag = await chrome.storage.session.get(SessionStore.key);
    const stored = bag[SessionStore.key] as CollectorState | undefined;
    return stored ? { ...EMPTY_STATE, ...stored } : { ...EMPTY_STATE };
  }

  async set(state: CollectorState): Promise<void> {
    await chrome.storage.session.set({ [SessionStore.key]: state });
  }

  async rawGet(key: string): Promise<Record<string, unknown>> {
    return (await chrome.storage.session.get(key)) as Record<string, unknown>;
  }

  async rawSet(key: string, value: unknown): Promise<void> {
    await chrome.storage.session.set({ [key]: value });
  }

  async rawRemove(key: string): Promise<void> {
    await chrome.storage.session.remove(key);
  }
}

/** In-memory store for unit tests and popup preview mode. */
export class MemoryStore implements StateStore {
  private state: CollectorState;
  private kv = new Map<string, unknown>();

  constructor(state: CollectorState = { ...EMPTY_STATE }) {
    this.state = state;
  }

  async get(): Promise<CollectorState> {
    return { ...this.state, observations: [...this.state.observations] };
  }

  async set(state: CollectorState): Promise<void> {
    this.state = { ...state, observations: [...state.observations] };
  }

  async rawGet(key: string): Promise<Record<string, unknown>> {
    return this.kv.has(key) ? { [key]: this.kv.get(key) } : {};
  }

  async rawSet(key: string, value: unknown): Promise<void> {
    this.kv.set(key, value);
  }

  async rawRemove(key: string): Promise<void> {
    this.kv.delete(key);
  }
}
