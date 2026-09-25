import axios, { AxiosError, AxiosHeaders, type AxiosRequestConfig } from "axios";

import { webConfig } from "@/lib/config";
import { STORAGE_NAMESPACE } from "@/lib/storage-namespace";
import { clearStoredAuthSession, getStoredAuthKey } from "@/stores/auth";

type RequestConfig = AxiosRequestConfig & { redirectOnUnauthorized?: boolean };
type ErrorPayload = { detail?: unknown; error?: string | { message?: string }; message?: string };

function errorMessageFromValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "";
  if (Array.isArray(value)) {
    const first = value.find((item) => item && typeof item === "object") as { msg?: unknown; loc?: unknown } | undefined;
    if (!first) return "";
    const loc = Array.isArray(first.loc) ? first.loc.slice(1).join(".") : "";
    const msg = typeof first.msg === "string" ? first.msg : "";
    return [loc, msg].filter(Boolean).join(": ");
  }
  const item = value as { error?: unknown; message?: unknown };
  if (typeof item.message === "string") return item.message;
  return errorMessageFromValue(item.error);
}

export const request = axios.create({ baseURL: webConfig.apiUrl });

request.interceptors.request.use(async (config) => {
  const headers = AxiosHeaders.from(config.headers);
  const authKey = await getStoredAuthKey();
  if (authKey && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${authKey}`);
  if (!headers.has("X-GMKRaw-Storage-Namespace")) headers.set("X-GMKRaw-Storage-Namespace", STORAGE_NAMESPACE);
  if (!headers.has("X-GMKRaw-Storage-Client")) headers.set("X-GMKRaw-Storage-Client", "namespaced-v3");
  config.headers = headers;
  return config;
});

request.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ErrorPayload>) => {
    const status = error.response?.status;
    const shouldClear = (error.config as RequestConfig | undefined)?.redirectOnUnauthorized !== false;
    if (status === 401 && shouldClear) {
      await clearStoredAuthSession();
      window.dispatchEvent(new CustomEvent("auth-unauthorized"));
    }
    const payload = error.response?.data;
    const message =
      errorMessageFromValue(payload?.detail) ||
      errorMessageFromValue(payload?.error) ||
      payload?.message ||
      error.message ||
      `请求失败 (${status || 500})`;
    return Promise.reject(new HttpRequestError(message, {
      status,
      payload,
    }));
  },
);

type RequestOptions = {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  redirectOnUnauthorized?: boolean;
  timeout?: number;
};

const inFlightGetRequests = new Map<string, Promise<unknown>>();

async function getRequestKey(
  path: string,
  headers: Record<string, string> | undefined,
  redirectOnUnauthorized: boolean,
  timeout: number | undefined,
) {
  const [pathname, rawQuery = ""] = path.split("?", 2);
  const params = new URLSearchParams(rawQuery);
  params.delete("_t");
  params.sort();
  const query = params.toString();
  const requestPath = query ? `${pathname}?${query}` : pathname;
  const headerScope = Object.entries(headers || {}).sort(([left], [right]) => left.localeCompare(right));
  const authScope = await getStoredAuthKey();
  return JSON.stringify([requestPath, authScope || "", headerScope, redirectOnUnauthorized, timeout || 0]);
}

export async function httpRequest<T>(path: string, options: RequestOptions = {}) {
  const { method = "GET", body, headers, redirectOnUnauthorized = true, timeout } = options;
  const normalizedMethod = method.toUpperCase();
  const execute = async () => {
    const response = await request.request<T>({
      url: path,
      method: normalizedMethod,
      data: body,
      headers,
      timeout,
      redirectOnUnauthorized,
    } as RequestConfig);
    return response.data;
  };
  if (normalizedMethod !== "GET") return execute();

  const requestKey = await getRequestKey(path, headers, redirectOnUnauthorized, timeout);
  const activeRequest = inFlightGetRequests.get(requestKey);
  if (activeRequest) return activeRequest as Promise<T>;

  const pendingRequest = execute();
  inFlightGetRequests.set(requestKey, pendingRequest);
  try {
    return await pendingRequest;
  } finally {
    if (inFlightGetRequests.get(requestKey) === pendingRequest) {
      inFlightGetRequests.delete(requestKey);
    }
  }
}
export class HttpRequestError extends Error {
  readonly status?: number;
  readonly code?: string;
  readonly payload?: unknown;

  constructor(message: string, options: { status?: number; code?: string; payload?: unknown } = {}) {
    super(message);
    this.name = "HttpRequestError";
    this.status = options.status;
    this.code = options.code;
    this.payload = options.payload;
  }
}
