// API client: unwrap {data, request_id} / {error} envelopes from the control plane.
export class ApiError extends Error {
  code: string;
  details: Record<string, unknown>;

  constructor(code: string, message: string, details: Record<string, unknown>) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

const TOKEN_KEY = "ppswitch.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const response = await fetch(path, { method, headers, body: payload });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = (data as { error?: { code?: string; message?: string; details?: Record<string, unknown> } }).error;
    throw new ApiError(error?.code ?? "UNKNOWN", error?.message ?? `HTTP ${response.status}`, error?.details ?? {});
  }
  return (data as { data: T }).data;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, form),
};
