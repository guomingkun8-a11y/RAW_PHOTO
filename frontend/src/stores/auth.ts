import localforage from "localforage";

export type AuthRole = "admin" | "user";

export type StoredAuthSession = {
  key: string;
  role: AuthRole;
  subjectId: string;
  username?: string;
  name: string;
};

export const AUTH_KEY_STORAGE_KEY = "gmkraw_auth_key";
export const AUTH_SESSION_STORAGE_KEY = "gmkraw_auth_session";

const authStorage = localforage.createInstance({
  name: "gmkraw",
  storeName: "auth",
});

function normalizeSession(value: unknown, fallbackKey = ""): StoredAuthSession | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<StoredAuthSession>;
  const key = String(candidate.key || fallbackKey || "").trim();
  const role = candidate.role === "admin" || candidate.role === "user" ? candidate.role : null;
  if (!key || !role) return null;
  return {
    key,
    role,
    subjectId: String(candidate.subjectId || "").trim(),
    username: String(candidate.username || "").trim(),
    name: String(candidate.name || "").trim(),
  };
}

export function getDefaultRouteForRole(_role: AuthRole) {
  return "/image";
}

export async function getStoredAuthKey() {
  return String((await authStorage.getItem<string>(AUTH_KEY_STORAGE_KEY)) || "").trim();
}

export async function getStoredAuthSession() {
  const [storedKey, storedSession] = await Promise.all([
    authStorage.getItem<string>(AUTH_KEY_STORAGE_KEY),
    authStorage.getItem<StoredAuthSession>(AUTH_SESSION_STORAGE_KEY),
  ]);
  const normalized = normalizeSession(storedSession, String(storedKey || ""));
  if (normalized) {
    if (normalized.key !== String(storedKey || "").trim()) {
      await authStorage.setItem(AUTH_KEY_STORAGE_KEY, normalized.key);
    }
    return normalized;
  }
  if (String(storedKey || "").trim()) await clearStoredAuthSession();
  return null;
}

export async function setStoredAuthSession(session: StoredAuthSession) {
  const normalized = normalizeSession(session);
  if (!normalized) {
    await clearStoredAuthSession();
    return;
  }
  await Promise.all([
    authStorage.setItem(AUTH_KEY_STORAGE_KEY, normalized.key),
    authStorage.setItem(AUTH_SESSION_STORAGE_KEY, normalized),
  ]);
}

export async function clearStoredAuthSession() {
  await Promise.all([
    authStorage.removeItem(AUTH_KEY_STORAGE_KEY),
    authStorage.removeItem(AUTH_SESSION_STORAGE_KEY),
  ]);
}
