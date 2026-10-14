/** MV3 service worker: capture orchestration + submission.
 *
 * Message API (popup -> SW, chrome.runtime.sendMessage):
 *   getState          -> { state, detachedWarning }
 *   setServer {url}   -> { state }
 *   pair {code}       -> { state }   (pairs against state.serverUrl)
 *   unpair            -> { state }
 *   start {tabId}     -> { state }   (attach debugger to ONE tab)
 *   stop              -> { state }   (detach; buffer is kept)
 *   clearBuffer       -> { state }
 *   submit            -> { state }   (upload buffered observations)
 *   preview           -> { candidates }
 */

import { CollectorEngine } from "./engine.js";
import { SessionStore } from "./store.js";
import { pair, submitObservations } from "../shared/api.js";
import type { CandidatePreview, CollectorState, Observation } from "../shared/types.js";
import * as cdp from "./cdp.js";

const AUTO_FLUSH_THRESHOLD = 50;
const SUBMIT_BATCH = 200; // server-side cap (PP_SWITCH_COLLECTOR_MAX_OBSERVATIONS)

const store = new SessionStore();
// the live engine is rebuilt from persisted observations after SW restarts
let engine = new CollectorEngine();
let detachedWarning = false;
let collectorToken: string | null = null;
let activeTabId: number | null = null;
let flushing = false;

interface RuntimeMessage {
  type: string;
  url?: string;
  code?: string;
  tabId?: number;
}

type Sender = (message: RuntimeMessage) => Promise<Record<string, unknown>>;

async function persist(): Promise<CollectorState> {
  const state = await store.get();
  state.observations = [...engine.observations];
  state.serverUrl = state.serverUrl || "http://127.0.0.1:8911";
  await store.set(state);
  return state;
}

function currentOrigin(url: string | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

async function startCapture(tabId: number): Promise<CollectorState> {
  const state = await store.get();
  const tab = await chrome.tabs.get(tabId);
  const origin = currentOrigin(tab.url);
  if (!origin || !/^https?:/.test(origin)) {
    throw new Error("Only http(s) pages can be collected");
  }
  // never collect traffic toward the control plane itself
  let serverOrigin = "";
  try {
    serverOrigin = new URL(state.serverUrl).origin;
  } catch {
    throw new Error("Invalid server URL");
  }
  engine = new CollectorEngine({ excludedOrigins: new Set([serverOrigin]) });
  engine.observations.push(...state.observations);
  await cdp.attach(tabId);
  await cdp.networkEnable(tabId);
  activeTabId = tabId;
  detachedWarning = false;
  state.session = {
    tabId,
    origin,
    attached: true,
    startedAt: Date.now(),
  };
  await store.set(state);
  return state;
}

async function stopCapture(): Promise<CollectorState> {
  const state = await store.get();
  if (activeTabId !== null) await cdp.detach(activeTabId);
  activeTabId = null;
  state.session = null;
  state.observations = [...engine.observations];
  await store.set(state);
  return state;
}

async function autoFlush(): Promise<void> {
  if (!collectorToken || flushing) return;
  const state = await store.get();
  if (!state.pairing || state.observations.length < AUTO_FLUSH_THRESHOLD) return;
  flushing = true;
  try {
    await doSubmit(state);
  } catch {
    // manual Submit will surface the error; buffer stays intact
  } finally {
    flushing = false;
  }
}

async function doSubmit(state: CollectorState): Promise<CollectorState> {
  if (!collectorToken || !state.pairing) throw new Error("Not paired with pp switch yet");
  if (state.observations.length === 0) throw new Error("Nothing to submit yet");
  let uploadedTotal = 0;
  let last;
  while (uploadedTotal < state.observations.length) {
    const batch: Observation[] = state.observations.slice(
      uploadedTotal,
      uploadedTotal + SUBMIT_BATCH,
    );
    last = await submitObservations(state.serverUrl, collectorToken, batch);
    uploadedTotal += batch.length;
  }
  const uploaded = state.observations.length;
  engine.dropUploaded(uploaded);
  state.observations = [...engine.observations];
  state.lastSubmit = {
    at: Date.now(),
    uploaded,
    candidates: last?.candidates ?? 0,
    operationsCreated: last?.operations_created ?? 0,
    operationsUpdated: last?.operations_updated ?? 0,
  };
  await store.set(state);
  return state;
}

const handlers: Record<string, Sender> = {
  async getState() {
    return { state: await persist(), detachedWarning };
  },

  async setServer({ url }) {
    const state = await store.get();
    const normalized = (url ?? "").trim().replace(/\/+$/, "");
    if (!/^https?:\/\//.test(normalized)) throw new Error("Server URL must start with http:// or https://");
    state.serverUrl = normalized;
    await store.set(state);
    return { state };
  },

  async pair({ code }) {
    const state = await store.get();
    const result = await pair(state.serverUrl, code ?? "");
    collectorToken = result.collector_token;
    state.pairing = {
      serverUrl: state.serverUrl,
      serviceId: result.service_id,
      tokenExpiresAt: result.token_expires_at,
    };
    await store.set(state);
    return { state };
  },

  async unpair() {
    collectorToken = null;
    const state = await store.get();
    state.pairing = null;
    await store.set(state);
    return { state };
  },

  async start({ tabId }) {
    return { state: await startCapture(tabId ?? -1) };
  },

  async stop() {
    return { state: await stopCapture() };
  },

  async clearBuffer() {
    engine.clear();
    return { state: await persist() };
  },

  async submit() {
    const state = await store.get();
    const next = await doSubmit(state);
    return { state: next };
  },

  async preview() {
    return { candidates: engine.preview() as CandidatePreview[] };
  },
};

chrome.runtime.onMessage.addListener((message: RuntimeMessage, _sender, sendResponse) => {
  const handler = handlers[message.type];
  if (!handler) {
    sendResponse({ ok: false, error: `Unknown message type: ${message.type}` });
    return false;
  }
  handler(message)
    .then((data) => sendResponse({ ok: true, ...data }))
    .catch((error: Error) => sendResponse({ ok: false, error: error.message }));
  return true; // async response
});

// CDP events -> engine. Tab-scoped: ignore events from other tabs.
chrome.debugger.onEvent.addListener((source, method, params) => {
  if (activeTabId === null || source.tabId !== activeTabId) return;
  const requestId = (params as { requestId?: string }).requestId ?? "";
  switch (method) {
    case "Network.requestWillBeSent":
      engine.onRequestWillBeSent(requestId, params as never);
      break;
    case "Network.responseReceived":
      engine.onResponseReceived(requestId, params as never);
      break;
    case "Network.loadingFailed":
      engine.onLoadingFailed(requestId);
      break;
    case "Network.loadingFinished":
      void engine
        .onLoadingFinished(
          requestId,
          (id) => cdp.getResponseBody(source.tabId!, id),
          (id) => cdp.getRequestPostData(source.tabId!, id),
        )
        .then(() => autoFlush())
        .catch(() => undefined);
      break;
    default:
      break;
  }
});

// The user clicked "Cancel" on the debug banner or closed the tab: the
// capture session ends, the collected buffer survives (disconnect recovery).
chrome.debugger.onDetach.addListener((source) => {
  if (activeTabId !== null && source.tabId === activeTabId) {
    detachedWarning = true;
    activeTabId = null;
    void persist().then(() =>
      chrome.runtime.sendMessage({ type: "captureDetached" }).catch(() => undefined),
    );
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (activeTabId === tabId) {
    activeTabId = null;
    detachedWarning = true;
    void persist();
  }
});

// SW (re)start: restore buffer + pairing from session storage; a capture
// session cannot survive SW suspension by itself - the popup will show
// the detached warning and the user re-attaches.
void (async () => {
  const state = await store.get();
  engine = new CollectorEngine({
    excludedOrigins: new Set(
      (() => {
        try {
          return [new URL(state.serverUrl).origin];
        } catch {
          return [];
        }
      })(),
    ),
  });
  engine.observations.push(...state.observations);
})();
