import axios, { AxiosError, AxiosHeaders, type AxiosRequestConfig } from "axios";

import webConfig from "@/constants/common-env";
import { clearStoredAuthSession, getStoredAuthKey } from "@/store/auth";

type RequestConfig = AxiosRequestConfig & {
  redirectOnUnauthorized?: boolean;
};

type ErrorPayload = {
  detail?: unknown;
  error?: string | { message?: string };
  message?: string;
};

function errorMessageFromValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (!value || typeof value !== "object") {
    return "";
  }
  if (Array.isArray(value)) {
    const first = value.find((item) => item && typeof item === "object") as
      | { msg?: unknown; loc?: unknown }
      | undefined;
    if (first) {
      const loc = Array.isArray(first.loc) ? first.loc.slice(1).join(".") : "";
      const msg = typeof first.msg === "string" ? first.msg : "";
      return [loc, msg].filter(Boolean).join(": ");
    }
    return "";
  }

  const item = value as { error?: unknown; message?: unknown };
  if (typeof item.message === "string") {
    return item.message;
  }
  return errorMessageFromValue(item.error);
}

export const request = axios.create({
  baseURL: webConfig.apiUrl.replace(/\/$/, ""),
});

request.interceptors.request.use(async (config) => {
  const nextConfig = { ...config };
  const authKey = await getStoredAuthKey();
  const headers = AxiosHeaders.from(nextConfig.headers);
  if (authKey && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${authKey}`);
  }
  nextConfig.headers = headers;
  return nextConfig;
});

request.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ErrorPayload>) => {
    const status = error.response?.status;
    const shouldClearSession = (error.config as RequestConfig | undefined)?.redirectOnUnauthorized !== false;
    if (status === 401 && shouldClearSession && typeof window !== "undefined") {
      await clearStoredAuthSession();
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
  const config: RequestConfig = {
    url: path,
    method,
    data: body,
    redirectOnUnauthorized,
  };
  if (headers) {
    config.headers = headers;
  }
  const response = await request.request<T>(config);
  return response.data;
}
