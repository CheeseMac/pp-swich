import { describe, expect, it } from "vitest";
import { coarsenPath, localKey, parseQueryNames, splitUrl } from "../src/shared/paths";

describe("coarsenPath", () => {
  it("collapses identifier-shaped segments before upload", () => {
    expect(coarsenPath("/api/customers/1024")).toBe("/api/customers/{id}");
    expect(coarsenPath("/api/customers/3fa85f64-5717-4562-b3fc-2c963f66afa6")).toBe(
      "/api/customers/{uuid}",
    );
    expect(coarsenPath("/files/a1b2c3d4e5f60718")).toBe("/files/{hash}");
  });

  it("keeps literal and version segments", () => {
    expect(coarsenPath("/api/v2/customers/search")).toBe("/api/v2/customers/search");
    expect(coarsenPath("/")).toBe("/");
  });

  it("ignores query strings", () => {
    expect(coarsenPath("/api/orders?page=2")).toBe("/api/orders");
  });
});

describe("parseQueryNames", () => {
  it("returns deduplicated parameter names only", () => {
    expect(parseQueryNames("?page=2&limit=50&page=3")).toEqual(["page", "limit"]);
    expect(parseQueryNames("?token=supersecret")).toEqual(["token"]);
    expect(parseQueryNames("")).toEqual([]);
  });
});

describe("localKey", () => {
  it("is stable across observation order and query order", () => {
    expect(localKey("get", "/api/orders", ["page", "q"])).toBe(
      localKey("GET", "/api/orders", ["q", "page"]),
    );
  });
});

describe("splitUrl", () => {
  it("accepts http(s) and rejects other schemes", () => {
    expect(splitUrl("https://crm.test/api/x?k=1")).toEqual({
      origin: "https://crm.test",
      pathname: "/api/x",
      search: "?k=1",
    });
    expect(splitUrl("chrome-extension://abc/popup.html")).toBeNull();
    expect(splitUrl("not a url")).toBeNull();
  });
});
