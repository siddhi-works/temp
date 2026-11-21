/** Small fetch wrapper. Every backend error has the shape {error: {code, message, detail}};
 * the message is already written for people, so the UI shows it as is. */

export class ApiError extends Error {
  code: string;
  status: number;
  detail: unknown;
  constructor(message: string, code: string, status: number, detail?: unknown) {
    super(message);
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
}

type Opts = {
  method?: string;
  json?: unknown;
  form?: FormData;
  token?: string; // student token, sent as ?t=
  staff?: string; // staff bearer token
  query?: Record<string, string | undefined>;
  timeoutMs?: number;
};

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const params = new URLSearchParams();
  if (opts.token) params.set("t", opts.token);
  for (const [k, v] of Object.entries(opts.query || {})) if (v !== undefined) params.set(k, v);
  const url = path + (params.toString() ? (path.includes("?") ? "&" : "?") + params.toString() : "");
  const headers: Record<string, string> = {};
  if (opts.staff) headers.Authorization = `Bearer ${opts.staff}`;
  let body: BodyInit | undefined;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  } else if (opts.form) {
    body = opts.form;
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 60000);
  let res: Response;
  try {
    res = await fetch(url, { method: opts.method || (body ? "POST" : "GET"), headers, body, signal: ctrl.signal });
  } catch (e) {
    const aborted = (e as Error).name === "AbortError";
    throw new ApiError(
      aborted ? "The server took too long to answer. Please try again." : "No connection to the server. Check your internet and try again.",
      aborted ? "timeout" : "network",
      0,
    );
  } finally {
    clearTimeout(timer);
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const err = data?.error;
    throw new ApiError(err?.message || `Something went wrong (error ${res.status}). Please try again.`, err?.code || "http_" + res.status, res.status, err?.detail);
  }
  return data as T;
}

/** Backend media URLs carry the backend origin; serve them through this site instead. */
export function rel(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const u = new URL(url, "http://x");
    return u.pathname + u.search;
  } catch {
    return url;
  }
}

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : "Something went wrong. Please try again.";
}

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function speakTime(hhmm: string | null | undefined): string {
  if (!hhmm) return "";
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h < 12 ? "AM" : "PM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m ? `${h12}:${String(m).padStart(2, "0")} ${suffix}` : `${h12} ${suffix}`;
}
