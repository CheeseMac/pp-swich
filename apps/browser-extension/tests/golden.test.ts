import { describe, expect, it } from "vitest";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { CollectorEngine, type CdpBody } from "../src/background/engine";

/**
 * Cross-language golden contract (FEAT-01):
 *
 * The observation JSON this extension produces is a wire format the
 * Python control plane parses (packages/discovery-sdk parsers/collector).
 * This test pins the TS side; packages/discovery-sdk's
 * test_collector_contract.py pins the Python side against the very same
 * committed fixture. Regenerate with REGEN_GOLDEN=1 pnpm test.
 */

const GOLDEN = resolve(__dirname, "../../../tests/fixtures/collector-golden/observations.json");

function json(body: unknown): CdpBody {
  return { body: JSON.stringify(body), base64Encoded: false };
}

async function buildGoldenObservations() {
  const engine = new CollectorEngine();
  const epoch = new Date(0);

  const capture = (
    id: string,
    url: string,
    method: string,
    type: string,
    postData: string | null,
    responseBody: unknown,
    status = 200,
  ) => {
    engine.onRequestWillBeSent(id, { request: { url, method, postData }, type });
    engine.onResponseReceived(id, {
      response: { status, mimeType: "application/json" },
      type,
    });
    return engine.onLoadingFinished(
      id,
      () => Promise.resolve(json(responseBody)),
      () => Promise.resolve(null),
      epoch,
    );
  };

  await Promise.all([
    // repeated detail views -> one merged template server-side
    capture(
      "c-1",
      "https://crm.example.test/api/customers/1024?verbose=true",
      "GET",
      "XHR",
      null,
      { id: "C-1024", display_name: "Acme", vip: true, score: 4.5 },
    ),
    capture(
      "c-2",
      "https://crm.example.test/api/customers/2048?verbose=true",
      "GET",
      "XHR",
      null,
      { id: "C-2048", display_name: "Globex", vip: false, score: 3.25 },
    ),
    capture(
      "c-3",
      "https://crm.example.test/api/customers/3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "GET",
      "XHR",
      null,
      { id: "C-4096", display_name: "Initech" },
    ),
    capture(
      "search",
      "https://crm.example.test/api/customers/search?page=2&limit=50",
      "POST",
      "Fetch",
      JSON.stringify({ q: "acme", limit: 10, filters: { region: "EU" } }),
      { results: [{ id: "R-1", total: 12.5 }] },
    ),
    capture(
      "gql",
      "https://crm.example.test/graphql",
      "POST",
      "Fetch",
      JSON.stringify({ operationName: "getCustomer", query: "query { x }" }),
      { data: { customer: { id: "C-1", tier: "gold" } } },
    ),
    capture(
      "err",
      "https://crm.example.test/api/orders/999",
      "DELETE",
      "XHR",
      null,
      { error: "not found" },
      404,
    ),
  ]);

  return engine.observations;
}

describe("golden observation contract", () => {
  it("engine output matches the committed fixture the Python parser tests against", async () => {
    const observations = await buildGoldenObservations();
    expect(observations).toHaveLength(6);
    if (process.env.REGEN_GOLDEN) {
      writeFileSync(GOLDEN, JSON.stringify(observations, null, 2) + "\n");
    }
    const committed = JSON.parse(readFileSync(GOLDEN, "utf-8"));
    expect(observations).toEqual(committed);
  });
});
