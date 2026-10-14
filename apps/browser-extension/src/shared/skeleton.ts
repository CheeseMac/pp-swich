/** Zero-value skeleton extraction: JSON values -> structure + types only.
 *
 * Privacy core of FEAT-01: every leaf is replaced by a type tag that
 * carries no data ("" / 0 / 0.5 / false / null). Keys and nesting are
 * kept - they are API structure. The server (packages/discovery-sdk)
 * infers JSON Schemas from these skeletons exactly like from samples,
 * and re-sanitizes them defensively; sensitive-named keys receive a
 * <REDACTED:...> marker server-side because their zero value is "".
 *
 * Nothing here can leak a value: strings, numbers and booleans are
 * discarded at the leaf.
 */

export const MAX_SKELETON_DEPTH = 8;
export const MAX_SKELETON_NODES = 2000;

export function toSkeleton(value: unknown, depth = 0, budget = { count: 0 }): unknown {
  if (budget.count >= MAX_SKELETON_NODES || depth >= MAX_SKELETON_DEPTH) {
    return ""; // over budget: degrade to a plain string leaf
  }
  budget.count++;
  if (value === null) return null;
  const t = typeof value;
  if (t === "string") return "";
  if (t === "boolean") return false;
  if (t === "number") return Number.isInteger(value) ? 0 : 0.5;
  if (Array.isArray(value)) {
    // one representative element keeps array item structure
    for (const element of value) {
      return [toSkeleton(element, depth + 1, budget)];
    }
    return [];
  }
  if (t === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      out[key] = toSkeleton(child, depth + 1, budget);
    }
    return out;
  }
  // functions / symbols / undefined never occur in parsed JSON
  return null;
}

/** Parse a body text into a skeleton; null when not JSON or oversized. */
export function parseBodyToSkeleton(
  text: string | null | undefined,
  maxBytes: number,
): unknown | null {
  if (!text || text.length > maxBytes) return null;
  const trimmed = text.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null;
  try {
    return toSkeleton(JSON.parse(trimmed));
  } catch {
    return null;
  }
}
