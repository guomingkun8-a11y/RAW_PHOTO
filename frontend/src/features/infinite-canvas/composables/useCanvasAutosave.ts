import { onBeforeUnmount, readonly, ref } from 'vue';

interface CanvasAutosaveOptions<T> {
  capture: () => T;
  persist: (snapshot: T) => Promise<unknown>;
  delayMs?: number;
  maxWaitMs?: number;
  retryDelayMs?: number;
  initialPaused?: boolean;
  isTerminalError?: (error: unknown) => boolean;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '画布保存失败';
}

export function useCanvasAutosave<T>(options: CanvasAutosaveOptions<T>) {
  const dirty = ref(false);
  const saving = ref(false);
  const lastError = ref('');
  const lastSavedAt = ref<Date>();
  const blockedByError = ref(false);

  const delayMs = Math.max(100, options.delayMs ?? 900);
  const maxWaitMs = Math.max(delayMs, options.maxWaitMs ?? 5_000);
  const retryDelayMs = Math.max(delayMs, options.retryDelayMs ?? 5_000);
  let paused = Boolean(options.initialPaused);
  let disposed = false;
  let contextVersion = 0;
  let revision = 0;
  let forceRequested = false;
  let saveTimer: number | undefined;
  let drainPromise: Promise<boolean> | undefined;
  let dirtySince = 0;
  let lastSavedFingerprint = '';

  function fingerprint(snapshot: T) {
    try {
      return JSON.stringify(snapshot);
    } catch {
      return '';
    }
  }

  function clearTimer() {
    if (saveTimer === undefined) return;
    window.clearTimeout(saveTimer);
    saveTimer = undefined;
  }

  function schedule(delay = delayMs) {
    clearTimer();
    if (paused || disposed || blockedByError.value || !dirty.value) return;
    const elapsed = dirtySince ? Date.now() - dirtySince : 0;
    const remaining = Math.max(0, maxWaitMs - elapsed);
    const nextDelay = Math.min(Math.max(0, delay), remaining);
    saveTimer = window.setTimeout(() => {
      saveTimer = undefined;
      void requestSave(false);
    }, nextDelay);
  }

  function markDirty() {
    if (paused || disposed) return;
    revision += 1;
    if (!dirty.value) dirtySince = Date.now();
    dirty.value = true;
    if (blockedByError.value) return;
    lastError.value = '';
    schedule();
  }

  async function drain() {
    let successful = true;

    while (!paused && !disposed && (dirty.value || forceRequested)) {
      const shouldForce = forceRequested;
      forceRequested = false;
      if (!dirty.value && !shouldForce) break;

      const savedContextVersion = contextVersion;
      const savedRevision = revision;
      const snapshot = options.capture();
      const snapshotFingerprint = fingerprint(snapshot);
      saving.value = true;

      if (snapshotFingerprint && snapshotFingerprint === lastSavedFingerprint) {
        if (savedRevision === revision) {
          dirty.value = false;
          dirtySince = 0;
        }
        continue;
      }

      try {
        await options.persist(snapshot);
        if (savedContextVersion !== contextVersion) continue;
        lastError.value = '';
        lastSavedAt.value = new Date();
        if (snapshotFingerprint) lastSavedFingerprint = snapshotFingerprint;
        if (savedRevision === revision) dirty.value = false;
        if (!dirty.value) dirtySince = 0;
      } catch (error) {
        if (savedContextVersion === contextVersion) {
          dirty.value = true;
          lastError.value = errorMessage(error);
          blockedByError.value = Boolean(options.isTerminalError?.(error));
        }
        successful = false;
        break;
      }
    }

    saving.value = false;
    if (dirty.value && !paused && !disposed && !blockedByError.value) schedule(successful ? delayMs : retryDelayMs);
    return successful && !dirty.value;
  }

  function requestSave(force: boolean) {
    clearTimer();
    if (drainPromise) return drainPromise;
    if (blockedByError.value) return Promise.resolve(false);
    if (paused || disposed) return Promise.resolve(!dirty.value);
    if (!dirty.value && !force) return Promise.resolve(true);
    if (force) forceRequested = true;

    drainPromise = drain().finally(() => {
      drainPromise = undefined;
    });
    return drainPromise;
  }

  function saveNow() {
    return requestSave(true);
  }

  function flush() {
    return requestSave(false);
  }

  function pause() {
    paused = true;
    clearTimer();
  }

  function resume() {
    if (disposed) return;
    paused = false;
    if (dirty.value) schedule();
  }

  function resetContext() {
    contextVersion += 1;
    revision = 0;
    forceRequested = false;
    blockedByError.value = false;
    clearTimer();
    dirty.value = false;
    dirtySince = 0;
    lastSavedFingerprint = '';
    lastError.value = '';
    lastSavedAt.value = undefined;
  }

  function resumeAsSaved(savedAt?: Date) {
    if (disposed) return;
    paused = false;
    blockedByError.value = false;
    dirty.value = false;
    dirtySince = 0;
    lastError.value = '';
    lastSavedAt.value = savedAt;
    lastSavedFingerprint = fingerprint(options.capture());
  }

  function dispose() {
    disposed = true;
    paused = true;
    contextVersion += 1;
    clearTimer();
  }

  onBeforeUnmount(dispose);

  return {
    dirty: readonly(dirty),
    saving: readonly(saving),
    lastError: readonly(lastError),
    lastSavedAt: readonly(lastSavedAt),
    blocked: readonly(blockedByError),
    markDirty,
    saveNow,
    flush,
    pause,
    resume,
    resetContext,
    resumeAsSaved,
    dispose,
  };
}
