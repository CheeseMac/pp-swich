import { describe, expect, it } from "vitest";
import { CollectorEngine, type CdpBody } from "../src/background/engine";

function jsonResponse(body: unknown): CdpBody {
  return { body: JSON.stringify(body), base64Encoded: false };
}

/** A realistic CRM interaction: list + search + detail + redirect. */
function feedCrmTraffic(engine: CollectorEngine): Promise<unknown>[] {
  const dones: Promise<unknown>[] = [];

  // 1) repeated GET /api/customers/:id -> same template, merged by preview
  for (const id of [1024, 2048]) {
    engine.onRequestWillBeSent("req-c-" + id, {
      request: { url: `https://crm.test/api/customers/${id}`, method: "GET" },
      type: "XHR",
    });
    engine.onResponseReceived("req-c-" + id, {
      response: { status: 200, mimeType: "application/json" },
      type: "XHR",
    });
    dones.push(
      engine.onLoadingFinished("req-c-" + id, () =>
        Promise.resolve(jsonResponse({ id: "C-" + id, display_name: "Acme " + id })),
      ),
    );
  }

  // 2) POST search with a body and a query param
  engine.onRequestWillBeSent("req-search", {
    request: {
      url: "https://crm.test/api/customers/search?verbose=true",
      method: "POST",
      postData: JSON.stringify({ q: "acme", limit: 10, filters: { region: "EU" } }),
    },
    type: "Fetch",
  });
  engine.onResponseReceived("req-search", {
    response: { status: 200, mimeType: "application/json" },
    type: "Fetch",
  });
  dones.push(
    engine.onLoadingFinished("req-search", () =>
      Promise.resolve(jsonResponse({ results: [{ id: "x", total: 1.5 }] })),
    ),
  );

  return dones;
}

describe("CollectorEngine", () => {
  it("turns CDP traffic into value-free observations", async () => {
    const engine = new CollectorEngine();
    await Promise.all(feedCrmTraffic(engine));

    expect(engine.observations).toHaveLength(3);
    for (const observation of engine.observations) {
      expect(observation.url.startsWith("https://crm.test/api/")).toBe(true);
      // zero values everywhere: bodies are skeletons, query values are ""
      const walk = (node: unknown): void => {
        if (typeof node === "string") expect(node).toBe("");
        else if (typeof node === "number") expect([0, 0.5]).toContain(node);
        else if (Array.isArray(node)) node.forEach(walk);
        else if (node && typeof node === "object")
          Object.values(node).forEach(walk);
      };
      walk(observation.request_body);
      walk(observation.response_body);
      for (const value of Object.values(observation.query)) expect(value).toBe("");
    }

    const search = engine.observations.find((o) => o.method === "POST");
    expect(search?.url).toBe("https://crm.test/api/customers/search");
    expect(search?.query).toEqual({ verbose: "" });
    expect(search?.request_body).toEqual({ q: "", limit: 0, filters: { region: "" } });
    expect(search?.response_body).toEqual({ results: [{ id: "", total: 0.5 }] });
  });

  it("merges repeated requests into one preview candidate", async () => {
    const engine = new CollectorEngine();
    await Promise.all(feedCrmTraffic(engine));
    const preview = engine.preview();
    expect(preview).toHaveLength(2);
    const detail = preview.find((p) => p.template === "/api/customers/{id}");
    expect(detail?.count).toBe(2);
    expect(detail?.method).toBe("GET");
    expect(detail?.responseTopKeys).toEqual(["display_name", "id"]);
    expect(detail?.statusCodes).toEqual([200]);
  });

  it("keeps only the final request of a redirect chain", async () => {
    const engine = new CollectorEngine();
    engine.onRequestWillBeSent("req-r", {
      request: { url: "https://crm.test/api/old", method: "GET" },
      redirectResponse: { status: 302 },
    });
    engine.onRequestWillBeSent("req-r", {
      request: { url: "https://crm.test/api/new", method: "GET" },
    });
    engine.onResponseReceived("req-r", {
      response: { status: 200, mimeType: "application/json" },
      type: "XHR",
    });
    await engine.onLoadingFinished("req-r", () =>
      Promise.resolve(jsonResponse({ ok: true })),
    );
    expect(engine.observations).toHaveLength(1);
    expect(engine.observations[0].url).toBe("https://crm.test/api/new");
  });

  it("never double-counts duplicate loadingFinished events", async () => {
    const engine = new CollectorEngine();
    engine.onRequestWillBeSent("req-d", {
      request: { url: "https://crm.test/api/x", method: "GET" },
      type: "XHR",
    });
    engine.onResponseReceived("req-d", {});
    const fetcher = () => Promise.resolve(jsonResponse({ a: 1 }));
    await engine.onLoadingFinished("req-d", fetcher);
    const again = await engine.onLoadingFinished("req-d", fetcher);
    expect(again).toBeNull();
    expect(engine.observations).toHaveLength(1);
  });

  it("drops loadingFailed requests", async () => {
    const engine = new CollectorEngine();
    engine.onRequestWillBeSent("req-f", {
      request: { url: "https://crm.test/api/x", method: "GET" },
      type: "XHR",
    });
    engine.onLoadingFailed("req-f");
    const result = await engine.onLoadingFinished("req-f", () => Promise.resolve(null));
    expect(result).toBeNull();
  });

  it("ignores non Fetch/XHR resources", async () => {
    const engine = new CollectorEngine();
    for (const [id, type] of [
      ["doc", "Document"],
      ["img", "Image"],
      ["script", "Script"],
    ] as const) {
      engine.onRequestWillBeSent(id, {
        request: { url: `https://crm.test/${id}.html`, method: "GET" },
        type,
      });
      engine.onResponseReceived(id, { response: { status: 200, mimeType: "text/html" }, type });
      const observation = await engine.onLoadingFinished(id, () =>
        Promise.resolve(jsonResponse({ nope: 1 })),
      );
      expect(observation).toBeNull();
    }
    expect(engine.observations).toHaveLength(0);
  });

  it("drops third-party origins when an allowedOrigins scope is set", async () => {
    const engine = new CollectorEngine({
      allowedOrigins: new Set(["https://crm.test"]),
    });
    for (const [id, url] of [
      ["own", "https://crm.test/api/orders"],
      ["tracker", "https://analytics.vendor.test/collect"],
      ["cdn", "https://static.vendor.test/api/config.json"],
    ] as const) {
      engine.onRequestWillBeSent(id, {
        request: { url, method: "GET" },
        type: "XHR",
      });
      engine.onResponseReceived(id, {
        response: { status: 200, mimeType: "application/json" },
        type: "XHR",
      });
      const observation = await engine.onLoadingFinished(id, () =>
        Promise.resolve(jsonResponse({ a: 1 })),
      );
      if (id === "own") {
        expect(observation).not.toBeNull();
      } else {
        expect(observation).toBeNull();
      }
    }
    expect(engine.observations).toHaveLength(1);
    expect(engine.observations[0].url).toBe("https://crm.test/api/orders");
  });

  it("never collects the control-plane origin", async () => {
    const engine = new CollectorEngine({
      excludedOrigins: new Set(["http://127.0.0.1:8911"]),
    });
    engine.onRequestWillBeSent("req-console", {
      request: { url: "http://127.0.0.1:8911/api/v1/operations", method: "GET" },
      type: "XHR",
    });
    engine.onResponseReceived("req-console", {
      response: { status: 200, mimeType: "application/json" },
      type: "XHR",
    });
    const observation = await engine.onLoadingFinished("req-console", () =>
      Promise.resolve(jsonResponse([])),
    );
    expect(observation).toBeNull();
  });

  it("extracts graphql operationName and separates templates", async () => {
    const engine = new CollectorEngine();
    for (const op of ["getCustomer", "searchOrders"]) {
      const id = "gql-" + op;
      engine.onRequestWillBeSent(id, {
        request: {
          url: "https://crm.test/graphql",
          method: "POST",
          postData: JSON.stringify({ operationName: op, query: "query { x }" }),
        },
        type: "Fetch",
      });
      engine.onResponseReceived(id, {
        response: { status: 200, mimeType: "application/json" },
        type: "Fetch",
      });
      await engine.onLoadingFinished(id, () =>
        Promise.resolve(jsonResponse({ data: { x: 1 } })),
      );
    }
    const operations = engine.observations.filter((o) => o.graphql_operation);
    expect(operations.map((o) => o.graphql_operation).sort()).toEqual([
      "getCustomer",
      "searchOrders",
    ]);
  });

  it("degrades gracefully when the response body is gone", async () => {
    const engine = new CollectorEngine();
    engine.onRequestWillBeSent("req-evicted", {
      request: { url: "https://crm.test/api/x", method: "GET" },
      type: "XHR",
    });
    engine.onResponseReceived("req-evicted", {
      response: { status: 204, mimeType: "application/json" },
      type: "XHR",
    });
    const observation = await engine.onLoadingFinished("req-evicted", () =>
      Promise.reject(new Error("No resource with given identifier")),
    );
    expect(observation).not.toBeNull();
    expect(observation?.response_body).toBeNull();
    expect(observation?.status).toBe(204);
  });

  it("fetches missing POST data through the fallback", async () => {
    const engine = new CollectorEngine();
    engine.onRequestWillBeSent("req-nopost", {
      request: { url: "https://crm.test/api/submit", method: "POST" },
      type: "Fetch",
    });
    engine.onResponseReceived("req-nopost", {
      response: { status: 200, mimeType: "application/json" },
      type: "Fetch",
    });
    const observation = await engine.onLoadingFinished(
      "req-nopost",
      () => Promise.resolve(jsonResponse({ ok: 1 })),
      () => Promise.resolve({ body: '{"amount": 5}', base64Encoded: false }),
    );
    expect(observation?.request_body).toEqual({ amount: 0 });
  });

  it("skips oversized bodies", async () => {
    const engine = new CollectorEngine({ maxBodyBytes: 20 });
    engine.onRequestWillBeSent("req-big", {
      request: {
        url: "https://crm.test/api/big",
        method: "POST",
        postData: JSON.stringify({ blob: "x".repeat(200) }),
      },
      type: "Fetch",
    });
    engine.onResponseReceived("req-big", {
      response: { status: 200, mimeType: "application/json" },
      type: "Fetch",
    });
    const observation = await engine.onLoadingFinished("req-big", () =>
      Promise.resolve({ body: "x".repeat(200), base64Encoded: false }),
    );
    expect(observation?.request_body).toBeNull();
    expect(observation?.response_body).toBeNull();
  });

  it("keeps the buffer across dropUploaded and clear operations", async () => {
    const engine = new CollectorEngine();
    await Promise.all(feedCrmTraffic(engine));
    engine.dropUploaded(3);
    expect(engine.observations).toHaveLength(0);
    expect(engine.preview()).toHaveLength(0);

    await Promise.all(feedCrmTraffic(engine));
    engine.clear();
    expect(engine.observations).toHaveLength(0);
  });
});
