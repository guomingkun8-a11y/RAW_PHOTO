import axios, { AxiosError, AxiosHeaders, type AxiosRequestConfig } from "axios";

import { webConfig } from "@/lib/config";
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
    return Promise.reject(new Error(message));
  },
);

type RequestOptions = {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  redirectOnUnauthorized?: boolean;
};

export async function httpRequest<T>(path: string, options: RequestOptions = {}) {
  const { method = "GET", body, headers, redirectOnUnauthorized = true } = options;
  const response = await request.request<T>({
    url: path,
    method,
    data: body,
    headers,
    redirectOnUnauthorized,
  } as RequestConfig);
  return response.data;
}
