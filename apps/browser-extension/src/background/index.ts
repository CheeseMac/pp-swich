/** MV3 service worker: chrome.* wiring around CollectorRuntime.
 *
 * All lifecycle logic (token persistence, buffer sync, auto-flush,
 * SW-restart recovery) lives in runtime.ts and is unit-tested there.
 * This file only: message API for the popup, CDP event plumbing,
 * debugger/tab lifecycle handling.
 *
 * Message API (popup -> SW, chrome.runtime.sendMessage):
 *   getState          -> { state }   (never contains the collector token)
 *   setServer {url}   -> { state }
 *   pair {code}       -> { state }
 *   unpair            -> { state }
 *   start {tabId}     -> { state }   (attach debugger to ONE tab; scope
 *                                     collection to that tab's origin)
 *   stop              -> { state }   (detach; buffer is kept)
 *   clearBuffer       -> { state }
 *   submit            -> { state }
 *   preview           -> { candidates }
 */

import { CollectorRuntime } from "./runtime.js";
import { SessionStore } from "./store.js";
import type { CandidatePreview } from "../shared/types.js";
import * as cdp from "./cdp.js";

const runtime = new CollectorRuntime({ store: new SessionStore() });
let activeTabId: number | null = null;

interface RuntimeMessage {
  type: string;
  url?: string;
  code?: string;
  tabId?: number;
}

function currentOrigin(url: string | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

async function startCapture(tabId: number) {
  const tab = await chrome.tabs.get(tabId);
  const origin = currentOrigin(tab.url);
  if (!origin || !/^https?:/.test(origin)) {
    throw new Error("Only http(s) pages can be collected");
  }
  const state = await runtime.startCapture(tabId, origin);
  await cdp.attach(tabId);
  await cdp.networkEnable(tabId);
  activeTabId = tabId;
  return state;
}

async function stopCapture() {
  if (activeTabId !== null) await cdp.detach(activeTabId);
  activeTabId = null;
  return runtime.stopCapture();
}

const handlers: Record<
  string,
  (message: RuntimeMessage) => Promise<unknown> | unknown
> = {
  getState: () => runtime.getState(),
  setServer: ({ url }) => runtime.setServerUrl(url ?? ""),
  pair: ({ code }) => runtime.pair(code ?? ""),
  unpair: () => runtime.unpair(),
  start: ({ tabId }) => startCapture(tabId ?? -1),
  stop: () => stopCapture(),
  clearBuffer: () => runtime.clearBuffer(),
  submit: () => runtime.submit(),
  preview: () => ({ candidates: runtime.engineRef.preview() as CandidatePreview[] }),
};

chrome.runtime.onMessage.addListener((message: RuntimeMessage, _sender, sendResponse) => {
  const handler = handlers[message.type];
  if (!handler) {
    sendResponse({ ok: false, error: `Unknown message type: ${message.type}` });
    return false;
  }
  Promise.resolve(handler(message))
    .then((data: unknown) =>
      sendResponse({ ok: true, ...(data as Record<string, unknown>) }),
    )
    .catch((error: Error) => sendResponse({ ok: false, error: error.message }));
  return true; // async response
});

// CDP events -> engine. Tab-scoped: ignore events from other tabs.
chrome.debugger.onEvent.addListener((source, method, params) => {
  if (activeTabId === null || source.tabId !== activeTabId) return;
  const requestId = (params as { requestId?: string }).requestId ?? "";
  switch (method) {
    case "Network.requestWillBeSent":
      runtime.engineRef.onRequestWillBeSent(requestId, params as never);
      break;
    case "Network.responseReceived":
      runtime.engineRef.onResponseReceived(requestId, params as never);
      break;
    case "Network.loadingFailed":
      runtime.engineRef.onLoadingFailed(requestId);
      break;
    case "Network.loadingFinished":
      void runtime.engineRef
        .onLoadingFinished(
          requestId,
          (id) => cdp.getResponseBody(source.tabId!, id),
          (id) => cdp.getRequestPostData(source.tabId!, id),
        )
        // FEAT-01-F1: persist the new observation BEFORE any flush
        // decision, then maybe auto-submit
        .then(() => runtime.onObservationComplete())
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
    activeTabId = null;
    void runtime.noteAttachLost().then(() =>
      chrome.runtime.sendMessage({ type: "captureDetached" }).catch(() => undefined),
    );
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (activeTabId === tabId) {
    activeTabId = null;
    void runtime.noteAttachLost();
  }
});

// SW (re)start: restore buffer, pairing and token from session storage;
// a capture session cannot survive SW suspension - the popup shows the
// detached state and the user re-attaches.
void runtime.restore();
