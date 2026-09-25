const DEFAULT_STORAGE_NAMESPACE = "gmkraw";

const configuredStorageNamespace = String(import.meta.env.VITE_STORAGE_NAMESPACE || "").trim();

export const STORAGE_NAMESPACE = configuredStorageNamespace || DEFAULT_STORAGE_NAMESPACE;

export function storageKey(key: string) {
  if (STORAGE_NAMESPACE === DEFAULT_STORAGE_NAMESPACE) return key;
  if (key.startsWith(`${DEFAULT_STORAGE_NAMESPACE}:`)) {
    return `${STORAGE_NAMESPACE}:${key.slice(DEFAULT_STORAGE_NAMESPACE.length + 1)}`;
  }
  if (key.startsWith(`${DEFAULT_STORAGE_NAMESPACE}_`)) {
    return `${STORAGE_NAMESPACE}_${key.slice(DEFAULT_STORAGE_NAMESPACE.length + 1)}`;
  }
  if (key.startsWith(`${DEFAULT_STORAGE_NAMESPACE}-`)) {
    return `${STORAGE_NAMESPACE}-${key.slice(DEFAULT_STORAGE_NAMESPACE.length + 1)}`;
  }
  if (key.startsWith("raw-")) {
    return `${STORAGE_NAMESPACE}-${key.slice(4)}`;
  }
  return `${STORAGE_NAMESPACE}:${key}`;
}

export function storageName(name = DEFAULT_STORAGE_NAMESPACE) {
  if (STORAGE_NAMESPACE === DEFAULT_STORAGE_NAMESPACE) return name;
  if (name === DEFAULT_STORAGE_NAMESPACE || name === "raw") return STORAGE_NAMESPACE;
  return `${STORAGE_NAMESPACE}:${name}`;
}

export const PROMPT_TEMPLATE_USE_STORAGE_KEY = storageKey("gmkraw:pending_prompt_template");
