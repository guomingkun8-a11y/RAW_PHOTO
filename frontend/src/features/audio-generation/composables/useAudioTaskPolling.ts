import { onBeforeUnmount } from "vue";

import { fetchAudioGenerationTasks } from "@/features/audio-generation/services/audio-generation-api";
import type { AudioGenerationTask } from "@/features/audio-generation/types/audio-generation";

const ACTIVE_STATUSES = new Set<AudioGenerationTask["status"]>(["queued", "running"]);

export function audioTaskNeedsPolling(task: AudioGenerationTask | null | undefined) {
  return Boolean(task && ACTIVE_STATUSES.has(task.status));
}

export function useAudioTaskPolling(
  onTask: (task: AudioGenerationTask) => void,
  onError: (error: unknown) => void,
) {
  let taskId = "";
  let timer = 0;
  let failures = 0;
  let disposed = false;

  function clearTimer() {
    window.clearTimeout(timer);
    timer = 0;
  }

  function nextDelay() {
    const base = document.hidden ? 10_000 : 2_500;
    return Math.min(30_000, base * 2 ** Math.min(failures, 3));
  }

  function schedule() {
    clearTimer();
    if (!disposed && taskId) timer = window.setTimeout(poll, nextDelay());
  }

  async function poll() {
    if (!taskId || disposed) return;
    const requestedId = taskId;
    try {
      const response = await fetchAudioGenerationTasks([requestedId]);
      if (disposed || requestedId !== taskId) return;
      const task = response.items[0];
      if (!task) throw new Error("音频任务不存在或已删除");
      failures = 0;
      onTask(task);
      if (audioTaskNeedsPolling(task)) schedule();
      else stop();
    } catch (error) {
      if (disposed || requestedId !== taskId) return;
      failures += 1;
      onError(error);
      schedule();
    }
  }

  function start(id: string, immediate = true) {
    taskId = id.trim();
    failures = 0;
    clearTimer();
    if (!taskId) return;
    if (immediate) void poll();
    else schedule();
  }

  function stop() {
    taskId = "";
    clearTimer();
  }

  function onVisibilityChange() {
    if (taskId) schedule();
  }

  document.addEventListener("visibilitychange", onVisibilityChange);
  onBeforeUnmount(() => {
    disposed = true;
    clearTimer();
    document.removeEventListener("visibilitychange", onVisibilityChange);
  });

  return { start, stop, poll };
}
