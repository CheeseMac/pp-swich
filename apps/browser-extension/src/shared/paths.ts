/** Coarse path templating and local preview grouping.
 *
 * The extension applies a COARSE identifier collapse before anything
 * leaves the browser (numeric / uuid / hex-hash segments become
 * placeholders), so raw record IDs never appear in an upload. The
 * server's ``normalize_path`` (packages/discovery-sdk) is the
 * authoritative normalizer and additionally handles email/JWT/mixed
 * shapes; it is idempotent on already-coarsened templates.
 */

const NUMERIC = /^\d+$/;
const UUID =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const HEX_HASH = /^[0-9a-fA-F]{16,}$/;

export function coarsenPath(pathname: string): string {
  const segments = pathname.split("?")[0].split("/").filter((s) => s !== "");
  const rendered = segments.map((segment) => {
    if (NUMERIC.test(segment)) return "{id}";
    if (UUID.test(segment)) return "{uuid}";
    if (HEX_HASH.test(segment)) return "{hash}";
    return segment;
  });
  return "/" + rendered.join("/");
}

export function parseQueryNames(search: string): string[] {
  if (!search || search === "?") return [];
  const clean = search.startsWith("?") ? search.slice(1) : search;
  const names = new Set<string>();
  for (const part of clean.split("&")) {
    if (!part) continue;
    const name = part.split("=", 1)[0];
    if (name) names.add(name);
  }
  return [...names];
}

/** Local preview key; server fingerprint (sha256) is authoritative. */
export function localKey(method: string, template: string, queryNames: string[]): string {
  return `${method.toUpperCase()}|${template}|${[...queryNames].sort().join(",")}`;
}

export interface UrlParts {
  origin: string;
  pathname: string;
  search: string;
}

export function splitUrl(url: string): UrlParts | null {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    return { origin: parsed.origin, pathname: parsed.pathname, search: parsed.search };
  } catch {
    return null;
  }
}
