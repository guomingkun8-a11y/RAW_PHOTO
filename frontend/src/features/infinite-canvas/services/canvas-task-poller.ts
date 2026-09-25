import {
  fetchImageTasks,
  fetchVideoGenerationTasks,
  type ImageTask,
  type VideoGenerationTask,
} from '@/lib/api';

type TaskKind = 'image' | 'video';
type CanvasTask = ImageTask | VideoGenerationTask;

interface TaskWaiter<T extends CanvasTask> {
  resolve: (task: T) => void;
  reject: (error: unknown) => void;
  onProgress?: (task: T) => void;
  signal?: AbortSignal;
  startedAt: number;
  onAbort?: () => void;
}

interface PendingTask<T extends CanvasTask> {
  missingCount: number;
  waiters: Set<TaskWaiter<T>>;
}

const TERMINAL_TASK_STATUSES = new Set(['success', 'error', 'canceled']);
const FOREGROUND_INTERVAL_MS = 2_000;
const BACKGROUND_INTERVAL_MS = 15_000;
const MAX_FOREGROUND_BACKOFF_MS = 30_000;
const MAX_BACKGROUND_BACKOFF_MS = 60_000;
const POLL_TIMEOUT_MS = 30 * 60 * 1_000;
const MAX_MISSING_POLLS = 3;

const pendingImages = new Map<string, PendingTask<ImageTask>>();
const pendingVideos = new Map<string, PendingTask<VideoGenerationTask>>();
let pollTimer: number | undefined;
let scheduledAt = 0;
let polling = false;
let consecutiveFailures = 0;

function abortError() {
  return new DOMException('Aborted', 'AbortError');
}

function baseInterval() {
  return typeof document !== 'undefined' && document.hidden
    ? BACKGROUND_INTERVAL_MS
    : FOREGROUND_INTERVAL_MS;
}

function nextInterval() {
  const base = baseInterval();
  const maximum = typeof document !== 'undefined' && document.hidden
    ? MAX_BACKGROUND_BACKOFF_MS
    : MAX_FOREGROUND_BACKOFF_MS;
  return Math.min(maximum, base * (2 ** consecutiveFailures));
}

function hasPendingTasks() {
  return pendingImages.size > 0 || pendingVideos.size > 0;
}

function schedulePoll(delay: number) {
  if (!hasPendingTasks()) return;
  const target = Date.now() + Math.max(0, delay);
  if (pollTimer !== undefined && scheduledAt <= target) return;
  if (pollTimer !== undefined) window.clearTimeout(pollTimer);
  scheduledAt = target;
  pollTimer = window.setTimeout(() => {
    pollTimer = undefined;
    scheduledAt = 0;
    void pollPendingTasks();
  }, Math.max(0, delay));
}

function removeWaiter<T extends CanvasTask>(
  taskId: string,
  pending: Map<string, PendingTask<T>>,
  waiter: TaskWaiter<T>,
) {
  const entry = pending.get(taskId);
  if (!entry) return;
  entry.waiters.delete(waiter);
  if (waiter.signal && waiter.onAbort) waiter.signal.removeEventListener('abort', waiter.onAbort);
  if (!entry.waiters.size) pending.delete(taskId);
}

function settleTask<T extends CanvasTask>(
  taskId: string,
  pending: Map<string, PendingTask<T>>,
  task: T,
) {
  const entry = pending.get(taskId);
  if (!entry) return;
  for (const waiter of [...entry.waiters]) {
    try {
      waiter.onProgress?.(task);
    } catch {
      // A rendering callback must not stop status polling for the task.
    }
    if (TERMINAL_TASK_STATUSES.has(task.status)) {
      removeWaiter(taskId, pending, waiter);
      waiter.resolve(task);
    }
  }
}

function markMissing<T extends CanvasTask>(taskId: string, pending: Map<string, PendingTask<T>>) {
  const entry = pending.get(taskId);
  if (!entry) return;
  entry.missingCount += 1;
  if (entry.missingCount < MAX_MISSING_POLLS) return;
  for (const waiter of [...entry.waiters]) {
    removeWaiter(taskId, pending, waiter);
    waiter.reject(new Error('原生成任务不存在或无权访问，请重新生成'));
  }
}

function rejectTimedOutWaiters<T extends CanvasTask>(pending: Map<string, PendingTask<T>>) {
  const now = Date.now();
  for (const [taskId, entry] of pending) {
    for (const waiter of [...entry.waiters]) {
      if (now - waiter.startedAt < POLL_TIMEOUT_MS) continue;
      removeWaiter(taskId, pending, waiter);
      waiter.reject(new Error('生成仍在后台运行，请稍后重新打开画布查看结果'));
    }
  }
}

async function pollImages() {
  const ids = [...pendingImages.keys()];
  if (!ids.length) return;
  const response = await fetchImageTasks(ids);
  const tasks = new Map(response.items.map((task) => [task.id, task]));
  for (const id of ids) {
    const task = tasks.get(id);
    if (task) {
      const entry = pendingImages.get(id);
      if (entry) entry.missingCount = 0;
      settleTask(id, pendingImages, task);
    } else {
      markMissing(id, pendingImages);
    }
  }
}

async function pollVideos() {
  const ids = [...pendingVideos.keys()];
  if (!ids.length) return;
  const response = await fetchVideoGenerationTasks(ids);
  const tasks = new Map(response.items.map((task) => [task.id, task]));
  for (const id of ids) {
    const task = tasks.get(id);
    if (task) {
      const entry = pendingVideos.get(id);
      if (entry) entry.missingCount = 0;
      settleTask(id, pendingVideos, task);
    } else {
      markMissing(id, pendingVideos);
    }
  }
}

async function pollPendingTasks() {
  if (polling || !hasPendingTasks()) return;
  polling = true;
  rejectTimedOutWaiters(pendingImages);
  rejectTimedOutWaiters(pendingVideos);
  try {
    const results = await Promise.allSettled([pollImages(), pollVideos()]);
    const failed = results.some((result) => result.status === 'rejected');
    consecutiveFailures = failed ? Math.min(consecutiveFailures + 1, 5) : 0;
  } finally {
    polling = false;
    if (hasPendingTasks()) schedulePoll(nextInterval());
  }
}

function waitForTask<T extends CanvasTask>(
  taskId: string,
  pending: Map<string, PendingTask<T>>,
  options: { signal?: AbortSignal; onProgress?: (task: T) => void },
) {
  return new Promise<T>((resolve, reject) => {
    if (options.signal?.aborted) {
      reject(abortError());
      return;
    }
    const waiter: TaskWaiter<T> = {
      resolve,
      reject,
      onProgress: options.onProgress,
      signal: options.signal,
      startedAt: Date.now(),
    };
    waiter.onAbort = () => {
      removeWaiter(taskId, pending, waiter);
      reject(abortError());
    };
    const entry = pending.get(taskId) || { missingCount: 0, waiters: new Set<TaskWaiter<T>>() };
    entry.waiters.add(waiter);
    pending.set(taskId, entry);
    options.signal?.addEventListener('abort', waiter.onAbort, { once: true });
    schedulePoll(0);
  });
}

export function waitForCanvasImageTask(
  taskId: string,
  options: { signal?: AbortSignal; onProgress?: (task: ImageTask) => void } = {},
) {
  return waitForTask(taskId, pendingImages, options);
}

export function waitForCanvasVideoTask(
  taskId: string,
  options: { signal?: AbortSignal; onProgress?: (task: VideoGenerationTask) => void } = {},
) {
  return waitForTask(taskId, pendingVideos, options);
}
