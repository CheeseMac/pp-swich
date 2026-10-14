/** Collector session state persistence.
 *
 * chrome.storage.session keeps the buffer, pairing and collector token
 * across service-worker suspensions; it is cleared when the browser
 * closes, so an unpaired collector leaves nothing on disk. Session
 * storage never syncs through the profile; the token's trust domain is
 * the local browser profile (same as any other extension credential).
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
}

/** In-memory store for unit tests and popup preview mode. */
export class MemoryStore implements StateStore {
  constructor(private state: CollectorState = { ...EMPTY_STATE }) {}

  async get(): Promise<CollectorState> {
    return { ...this.state, observations: [...this.state.observations] };
  }

  async set(state: CollectorState): Promise<void> {
    this.state = { ...state, observations: [...state.observations] };
  }
}
