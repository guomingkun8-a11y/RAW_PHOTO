const apiUrl = String(import.meta.env.VITE_API_URL || (import.meta.env.DEV ? "" : "")).replace(/\/$/, "");

export const webConfig = {
  apiUrl,
  appVersion: __APP_VERSION__,
};
