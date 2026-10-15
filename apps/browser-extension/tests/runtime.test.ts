import { describe, expect, it } from "vitest";
import { CollectorRuntime, AUTO_FLUSH_THRESHOLD } from "../src/background/runtime";
import { MemoryStore } from "../src/background/store";
import type { CollectorState } from "../src/shared/types";

/** fake control-plane: counts uploads, returns canned envelopes */
function fakeBackend() {
  const uploads: Array<{ token: string; observations: unknown[] }> = [];
  const pairs: string[] = [];
  const fetchImpl = async (input: string, init?: RequestInit): Promise<Response> => {
    if (input.endsWith("/api/v1/collector/pair")) {
      const body = JSON.parse(String(init?.body)) as { pairing_code: string };
      pairs.push(body.pairing_code);
      return new Response(
        JSON.stringify({
          data: {
            collector_token: "ppc-test-token",
            service_id: "11111111-2222-3333-4444-555555555555",
            token_expires_at: new Date(Date.now() + 3600_000).toISOString(),
          },
        }),
        { status: 200 },
      );
    }
    if (input.endsWith("/api/v1/collector/observations")) {
      const auth = String((init?.headers as Record<string, string>)?.Authorization ?? "");
      const body = JSON.parse(String(init?.body)) as { observations: unknown[] };
      uploads.push({ token: auth.replace("Bearer ", ""), observations: body.observations });
      return new Response(
        JSON.stringify({
          data: {
            job_id: "job",
            state: "completed",
            candidates: 1,
            operations_created: 1,
            operations_updated: 0,
            observations_created: body.observations.length,
          },
        }),
        { status: 201 },
      );
    }
    throw new Error(`unexpected fetch ${input}`);
  };
  return { uploads, pairs, fetchImpl };
}

function observationFor(n: number) {
  return {
    url: `https://crm.example.test/api/orders/${n}`,
    method: "GET",
    status: 200,
    query: {},
    request_body: null,
    response_body: { id: "" },
    graphql_operation: null,
    observed_at: 0,
  };
}

describe("CollectorRuntime (FEAT-01-F1)", () => {
  it("survives SW teardown: pair, collect, restore in a new runtime, submit once", async () => {
    const backend = fakeBackend();
    const store = new MemoryStore();

    // --- SW lifetime #1: pair and collect
    const first = new CollectorRuntime({ store, fetchImpl: backend.fetchImpl });
    await first.restore();
    await first.pair("ABCD2345");
    await first.startCapture(7, "https://crm.example.test");
    for (let i = 0; i < 3; i++) {
      first.engineRef.observations.push(observationFor(i));
      await first.onObservationComplete(); // persist-then-flush contract
    }
    // no auto flush below the threshold
    expect(backend.uploads).toHaveLength(0);
    const persisted = await store.get();
    expect(persisted.observations).toHaveLength(3);
    expect(persisted.pairing?.serviceId).toBe("11111111-2222-3333-4444-555555555555");

    // --- SW dies (module state lost). Only the store survives.
    const second = new CollectorRuntime({ store, fetchImpl: backend.fetchImpl });
    await second.restore();
    // token recovered from session storage, not re-paired
    expect(backend.pairs).toHaveLength(1);
    expect(second.engineRef.observations).toHaveLength(3);
    // capture session reopens detached
    expect((await second.getState()).session?.attached).toBe(false);

    // --- submit after recovery: exactly the buffered observations, once
    const state = await second.submit();
    expect(backend.uploads).toHaveLength(1);
    expect(backend.uploads[0].observations).toHaveLength(3);
    expect(backend.uploads[0].token).toBe("ppc-test-token");
    expect(state.observations).toHaveLength(0);
    expect(state.lastSubmit?.uploaded).toBe(3);

    // a THIRD runtime after another SW bounce has nothing left to upload
    const third = new CollectorRuntime({ store, fetchImpl: backend.fetchImpl });
    await third.restore();
    await expect(third.submit()).rejects.toThrow("Nothing to submit yet");
    expect(backend.uploads).toHaveLength(1); // no double upload
  });

  it("auto-flushes after the threshold once observations are persisted", async () => {
    const backend = fakeBackend();
    const store = new MemoryStore();
    const runtime = new CollectorRuntime({ store, fetchImpl: backend.fetchImpl });
    await runtime.restore();
    await runtime.pair("ABCD2345");
    await runtime.startCapture(7, "https://crm.example.test");
    for (let i = 0; i < AUTO_FLUSH_THRESHOLD; i++) {
      runtime.engineRef.observations.push(observationFor(i));
      await runtime.onObservationComplete();
    }
    expect(backend.uploads).toHaveLength(1);
    expect(backend.uploads[0].observations).toHaveLength(AUTO_FLUSH_THRESHOLD);
    // buffer drained, store in sync
    expect((await runtime.getState()).observations).toHaveLength(0);
  });

  it("clears the pairing when the token expired while the SW was down", async () => {
    const backend = fakeBackend();
    const store = new MemoryStore();
    const first = new CollectorRuntime({ store, fetchImpl: backend.fetchImpl });
    await first.restore();
    await first.pair("ABCD2345");
    // force the token into the past directly in the store
    const state: CollectorState = await store.get();
    state.pairing!.tokenExpiresAt = new Date(Date.now() - 1000).toISOString();
    await store.set(state);

    const second = new CollectorRuntime({ store, fetchImpl: backend.fetchImpl });
    await second.restore();
    const restored = await second.getState();
    expect(restored.pairing).toBeNull(); // popup can no longer claim "connected"
    await expect(second.submit()).rejects.toThrow("Not paired");
  });

  it("unpair removes the token from session storage", async () => {
    const backend = fakeBackend();
    const store = new MemoryStore();
    const runtime = new CollectorRuntime({ store, fetchImpl: backend.fetchImpl });
    await runtime.restore();
    await runtime.pair("ABCD2345");
    expect(Object.keys(await store.rawGet("collectorToken"))).toHaveLength(1);
    await runtime.unpair();
    expect(Object.keys(await store.rawGet("collectorToken"))).toHaveLength(0);
    const state = await runtime.getState();
    expect(state.pairing).toBeNull();
  });

  it("keeps observations collected before a detach and submits them afterwards", async () => {
    const backend = fakeBackend();
    const store = new MemoryStore();
    const runtime = new CollectorRuntime({ store, fetchImpl: backend.fetchImpl });
    await runtime.restore();
    await runtime.pair("ABCD2345");
    await runtime.startCapture(7, "https://crm.example.test");
    runtime.engineRef.observations.push(observationFor(1));
    await runtime.onObservationComplete();
    await runtime.noteAttachLost(); // banner cancelled / tab closed
    const detached = await runtime.getState();
    expect(detached.session?.attached).toBe(false);
    expect(detached.observations).toHaveLength(1); // buffer survives
    const submitted = await runtime.submit();
    expect(submitted.lastSubmit?.uploaded).toBe(1);
  });
});
