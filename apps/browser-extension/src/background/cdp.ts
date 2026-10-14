/** Typed chrome.debugger helpers for the Network domain.
 *
 * chrome.debugger surfaces the Chrome DevTools Protocol. Attaching shows
 * the user a persistent "started debugging this tab" banner - the
 * authorization signal that FEAT-01 collects only where the user can
 * SEE that debugging is active.
 */

export const DEBUGGER_VERSION = "1.3";

export interface CdpError extends Error {
  chromeError?: string;
}

export function cdpError(message: string): CdpError {
  const error = new Error(message) as CdpError;
  error.chromeError = message;
  return error;
}

export async function attach(tabId: number): Promise<void> {
  try {
    await chrome.debugger.attach({ tabId }, DEBUGGER_VERSION);
  } catch (error) {
    throw cdpError(String((error as { message?: string }).message ?? error));
  }
}

export async function detach(tabId: number): Promise<void> {
  try {
    await chrome.debugger.detach({ tabId });
  } catch {
    // already detached (tab closed, user clicked "Cancel")
  }
}

export async function networkEnable(tabId: number): Promise<void> {
  await sendCommand(tabId, "Network.enable", {});
}

export async function sendCommand(
  tabId: number,
  method: string,
  params: Record<string, unknown>,
): Promise<unknown> {
  try {
    return await chrome.debugger.sendCommand({ tabId }, method, params);
  } catch (error) {
    throw cdpError(String((error as { message?: string }).message ?? error));
  }
}

export async function getResponseBody(
  tabId: number,
  requestId: string,
): Promise<{ body: string; base64Encoded: boolean } | null> {
  try {
    const result = (await sendCommand(tabId, "Network.getResponseBody", {
      requestId,
    })) as { body?: string; base64Encoded?: boolean } | undefined;
    if (!result || typeof result.body !== "string") return null;
    return { body: result.body, base64Encoded: !!result.base64Encoded };
  } catch {
    return null; // body evicted / not retained - degrade gracefully
  }
}

export async function getRequestPostData(
  tabId: number,
  requestId: string,
): Promise<{ body: string; base64Encoded: boolean } | null> {
  try {
    const result = (await sendCommand(tabId, "Network.getRequestPostData", {
      requestId,
    })) as { postData?: string } | undefined;
    if (!result || typeof result.postData !== "string") return null;
    return { body: result.postData, base64Encoded: false };
  } catch {
    return null;
  }
}
