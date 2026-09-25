import { computed, onBeforeUnmount, ref, shallowRef } from 'vue';

interface CanvasHistoryOptions<T> {
  capture: () => T;
  restore: (snapshot: T) => void;
  limit?: number;
  debounceMs?: number;
}

function cloneSnapshot<T>(snapshot: T): T {
  if (typeof structuredClone === 'function') return structuredClone(snapshot);
  return JSON.parse(JSON.stringify(snapshot)) as T;
}

function fingerprint(value: unknown) {
  return JSON.stringify(value);
}

export function useCanvasHistory<T>(options: CanvasHistoryOptions<T>) {
  const past = shallowRef<T[]>([]);
  const future = shallowRef<T[]>([]);
  const applying = ref(false);
  const limit = Math.max(1, options.limit ?? 50);
  const debounceMs = Math.max(100, options.debounceMs ?? 450);
  let current: T | undefined;
  let currentFingerprint = '';
  let captureTimer: number | undefined;

  const canUndo = computed(() => past.value.length > 0);
  const canRedo = computed(() => future.value.length > 0);

  function clearTimer() {
    if (captureTimer === undefined) return;
    window.clearTimeout(captureTimer);
    captureTimer = undefined;
  }

  function setCurrent(snapshot: T) {
    current = cloneSnapshot(snapshot);
    currentFingerprint = fingerprint(current);
  }

  function reset(snapshot = options.capture()) {
    clearTimer();
    past.value = [];
    future.value = [];
    setCurrent(snapshot);
  }

  function commit() {
    clearTimer();
    const next = cloneSnapshot(options.capture());
    const nextFingerprint = fingerprint(next);
    if (current === undefined) {
      setCurrent(next);
      return false;
    }
    if (nextFingerprint === currentFingerprint) return false;
    past.value = [...past.value, cloneSnapshot(current)].slice(-limit);
    future.value = [];
    current = next;
    currentFingerprint = nextFingerprint;
    return true;
  }

  function scheduleCommit() {
    clearTimer();
    captureTimer = window.setTimeout(() => {
      captureTimer = undefined;
      commit();
    }, debounceMs);
  }

  function replaceCurrent(snapshot = options.capture()) {
    clearTimer();
    setCurrent(snapshot);
    future.value = [];
  }

  function applySnapshot(snapshot: T) {
    applying.value = true;
    try {
      options.restore(cloneSnapshot(snapshot));
    } finally {
      applying.value = false;
    }
  }

  function undo() {
    commit();
    const target = past.value.at(-1);
    if (!target || current === undefined) return false;
    past.value = past.value.slice(0, -1);
    future.value = [cloneSnapshot(current), ...future.value].slice(0, limit);
    setCurrent(target);
    applySnapshot(target);
    return true;
  }

  function redo() {
    clearTimer();
    const target = future.value[0];
    if (!target || current === undefined) return false;
    future.value = future.value.slice(1);
    past.value = [...past.value, cloneSnapshot(current)].slice(-limit);
    setCurrent(target);
    applySnapshot(target);
    return true;
  }

  onBeforeUnmount(clearTimer);

  return {
    canUndo,
    canRedo,
    applying,
    commit,
    scheduleCommit,
    replaceCurrent,
    reset,
    undo,
    redo,
  };
}
