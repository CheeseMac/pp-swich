/** Popup controller: renders state, sends control messages to the SW. */

import type { CandidatePreview, CollectorState } from "../shared/types.js";

// Demo mode (popup.html?demo=1): renders the real UI against canned state
// when no extension context exists - used for screenshots and UI preview
// outside Chrome. Never active inside the extension (chrome.runtime exists).
if (!globalThis.chrome?.runtime) {
  const state: CollectorState = {
    serverUrl: "http://127.0.0.1:8911",
    pairing: {
      serverUrl: "http://127.0.0.1:8911",
      serviceId: "8f14e45f-ceea-467f-abc9-3d5b2e94b1e2",
      tokenExpiresAt: new Date(Date.now() + 7 * 3600_000).toISOString(),
    },
    session: {
      tabId: 1,
      origin: "https://crm.example.test",
      attached: true,
      startedAt: Date.now() - 90_000,
    },
    observations: Array.from({ length: 14 }, () => ({
      url: "https://crm.example.test/api/orders",
      method: "GET",
      status: 200,
      query: { page: "" },
      request_body: null,
      response_body: null,
      graphql_operation: null,
      observed_at: Date.now(),
    })),
    lastSubmit: {
      at: Date.now() - 30_000,
      uploaded: 18,
      candidates: 5,
      operationsCreated: 4,
      operationsUpdated: 1,
    },
  };
  const candidates: CandidatePreview[] = [
    {
      key: "GET|/api/customers/{id}|verbose",
      method: "GET",
      template: "/api/customers/{id}",
      queryNames: ["verbose"],
      responseTopKeys: ["display_name", "id", "score", "vip"],
      statusCodes: [200],
      count: 7,
    },
    {
      key: "POST|/api/customers/search|limit,page",
      method: "POST",
      template: "/api/customers/search",
      queryNames: ["limit", "page"],
      responseTopKeys: ["results"],
      statusCodes: [200],
      count: 4,
    },
    {
      key: "POST|/graphql#getCustomer|",
      method: "POST",
      template: "/graphql#getCustomer",
      queryNames: [],
      responseTopKeys: ["data"],
      statusCodes: [200],
      count: 3,
    },
  ];
  (globalThis as { chrome?: object }).chrome = {
    runtime: {
      lastError: null,
      sendMessage: (
        message: { type: string },
        callback: (response: unknown) => void,
      ) => {
        if (message.type === "getState") callback({ ok: true, state, detachedWarning: false });
        else if (message.type === "preview") callback({ ok: true, candidates });
        else callback({ ok: true, state });
      },
      onMessage: { addListener: () => undefined },
    },
    tabs: { query: async () => [{ id: 1, url: "https://crm.example.test/customers" }] },
  };
}

type MessageResponse = { ok: true; state?: CollectorState; candidates?: CandidatePreview[]; detachedWarning?: boolean } | { ok: false; error: string };

function send(message: Record<string, unknown>): Promise<MessageResponse> {
  return new Promise((resolve) => {
    void chrome.runtime.sendMessage(message, (response: MessageResponse) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: chrome.runtime.lastError.message ?? "background unavailable" });
        return;
      }
      resolve(response);
    });
  });
}

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

const els = {
  badge: $("pairing-badge"),
  serverUrl: $("server-url") as HTMLInputElement,
  pairingCode: $("pairing-code") as HTMLInputElement,
  btnPair: $<HTMLButtonElement>("btn-pair"),
  btnUnpair: $<HTMLButtonElement>("btn-unpair"),
  pairingDetail: $("pairing-detail"),
  btnStart: $<HTMLButtonElement>("btn-start"),
  btnStop: $<HTMLButtonElement>("btn-stop"),
  captureStatus: $("capture-status"),
  detachedWarning: $("detached-warning"),
  candidateCount: $("candidate-count"),
  candidateList: $("candidate-list"),
  btnSubmit: $<HTMLButtonElement>("btn-submit"),
  btnClear: $<HTMLButtonElement>("btn-clear"),
  submitStatus: $("submit-status"),
};

function render(state: CollectorState, detachedWarning: boolean): void {
  if (!els.serverUrl.value) els.serverUrl.value = state.serverUrl;

  const paired = !!state.pairing;
  els.badge.textContent = paired ? "已连接" : "未连接";
  els.badge.classList.toggle("paired", paired);
  els.btnPair.hidden = paired;
  els.btnUnpair.hidden = !paired;
  els.pairingDetail.textContent = paired
    ? `服务 ${state.pairing!.serviceId.slice(0, 8)}… · 令牌有效期至 ${new Date(
        state.pairing!.tokenExpiresAt,
      ).toLocaleTimeString()}`
    : "在控制台 Discovery 页选择服务并生成配对码，然后粘贴到上面。";

  const live = !!state.session?.attached;
  els.btnStart.hidden = live;
  els.btnStop.hidden = !live;
  els.captureStatus.textContent = live
    ? `采集中 · ${state.observations.length} 条观察 · ${state.session!.origin}`
    : state.observations.length > 0
      ? `未在采集 · 缓冲区有 ${state.observations.length} 条观察`
      : "未在采集。";
  els.captureStatus.classList.toggle("live", live);
  els.detachedWarning.hidden = !detachedWarning;

  els.btnSubmit.disabled = !paired || state.observations.length === 0;

  if (state.lastSubmit) {
    const s = state.lastSubmit;
    els.submitStatus.textContent = s.error
      ? `上次提交失败：${s.error}`
      : `上次提交 ${s.uploaded} 条 → ${s.candidates} 个候选 · 新建 ${s.operationsCreated} · 更新 ${s.operationsUpdated}（${new Date(s.at).toLocaleTimeString()}）`;
  }
}

async function refresh(): Promise<void> {
  const response = await send({ type: "getState" });
  if (!response.ok || !response.state) {
    els.captureStatus.textContent = `后台服务不可用：${"error" in response ? response.error : "无状态返回"}`;
    return;
  }
  render(response.state, !!response.detachedWarning);
  const preview = await send({ type: "preview" });
  if (preview.ok) renderCandidates(preview.candidates ?? []);
}

function renderCandidates(candidates: CandidatePreview[]): void {
  els.candidateCount.textContent = candidates.length ? `(${candidates.length})` : "";
  if (candidates.length === 0) {
    els.candidateList.innerHTML = '<p class="muted">还没有观察。</p>';
    return;
  }
  els.candidateList.innerHTML = "";
  for (const candidate of candidates) {
    const item = document.createElement("div");
    item.className = "candidate";
    const route = document.createElement("div");
    route.className = "route";
    const method = document.createElement("span");
    method.className = "method";
    method.textContent = candidate.method;
    route.append(method, candidate.template);
    const meta = document.createElement("div");
    meta.className = "meta";
    const bits = [`${candidate.count} 次观察`, `状态 ${candidate.statusCodes.join("/")}`];
    if (candidate.queryNames.length) bits.push(`参数 ${candidate.queryNames.join(", ")}`);
    if (candidate.responseTopKeys.length)
      bits.push(`响应字段 ${candidate.responseTopKeys.slice(0, 5).join(", ")}`);
    meta.textContent = bits.join(" · ");
    item.append(route, meta);
    els.candidateList.append(item);
  }
}

els.serverUrl.addEventListener("change", async () => {
  const response = await send({ type: "setServer", url: els.serverUrl.value });
  if (!response.ok) els.pairingDetail.textContent = response.error;
});

els.btnPair.addEventListener("click", async () => {
  els.btnPair.disabled = true;
  els.pairingDetail.textContent = "连接中…";
  const response = await send({ type: "pair", code: els.pairingCode.value });
  els.btnPair.disabled = false;
  if (!response.ok) {
    els.pairingDetail.textContent = `连接失败：${response.error}`;
    return;
  }
  els.pairingCode.value = "";
  await refresh();
});

els.btnUnpair.addEventListener("click", async () => {
  await send({ type: "unpair" });
  await refresh();
});

els.btnStart.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  const response = await send({ type: "start", tabId: tab.id });
  if (!response.ok) els.captureStatus.textContent = `无法开始：${response.error}`;
  await refresh();
});

els.btnStop.addEventListener("click", async () => {
  await send({ type: "stop" });
  await refresh();
});

els.btnSubmit.addEventListener("click", async () => {
  els.btnSubmit.disabled = true;
  els.submitStatus.textContent = "提交中…";
  const response = await send({ type: "submit" });
  if (!response.ok) els.submitStatus.textContent = `提交失败：${response.error}`;
  await refresh();
});

els.btnClear.addEventListener("click", async () => {
  await send({ type: "clearBuffer" });
  await refresh();
});

chrome.runtime.onMessage.addListener((message: { type: string }) => {
  if (message.type === "captureDetached") void refresh();
});

void refresh();
// live refresh while the popup is open and capturing
setInterval(() => void refresh(), 1500);
