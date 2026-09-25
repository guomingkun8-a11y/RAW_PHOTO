import { onBeforeUnmount, ref, watch } from "vue";

import { fetchVideoGenerationTasks, type VideoGenerationTask } from "@/lib/api";

export function videoTaskNeedsPolling(task: VideoGenerationTask) {
  return task.status === "queued" || task.status === "running"
    || task.cancellation_pending === true;
}

export function videoTaskDeletionReason(task: VideoGenerationTask) {
  if (task.cancellation_pending) return "取消后仍在核对上游结果和费用，暂不能删除";
  if (task.reconciliation_required) return "提交结果待核对，暂不能删除或重新生成";
  return videoTaskNeedsPolling(task) ? "进行中的视频任务无法删除，请先取消任务或等待任务完成" : "";
}

export function useVideoAssetRefresh(
  getTasks: () => VideoGenerationTask[],
  applyUpdates: (tasks: VideoGenerationTask[]) => void,
) {
  const inFlight = new Map<string, Promise<VideoGenerationTask>>();
  const autoRefreshed = new Set<string>();
  let disposed = false;
  onBeforeUnmount(() => { disposed = true; });

  function refresh(id: string): Promise<VideoGenerationTask> {
    const existing = inFlight.get(id);
    if (existing) return existing;
    const task = getTasks().find((item) => item.id === id);
    const request = fetchVideoGenerationTasks([id]).then((response) => {
      const updated = response.items.find((item) => item.id === id);
      if (!updated) throw new Error("视频不存在或已被删除");
      if (!disposed && getTasks().find((item) => item.id === id) === task) applyUpdates([updated]);
      return updated;
    }).finally(() => inFlight.delete(id));
    inFlight.set(id, request);
    return request;
  }

  function onMediaError(id: string) {
    // A corrupt media file must not cause an endless signed-URL refresh loop.
    if (!id || autoRefreshed.has(id)) return;
    autoRefreshed.add(id);
    void refresh(id).catch(() => {});
  }

  return { refresh, onMediaError };
}

export function useVideoTaskPolling(
  getTasks: () => VideoGenerationTask[],
  applyUpdates: (tasks: VideoGenerationTask[]) => void,
) {
  const error = ref("");
  let timer = 0;
  let failures = 0;
  let disposed = false;
  let inFlight: Promise<void> | null = null;

  function schedule() {
    window.clearTimeout(timer);
    timer = 0;
    if (disposed || inFlight || !getTasks().some(videoTaskNeedsPolling)) return;
    const delay = Math.max(document.hidden ? 60_000 : 5_000, Math.min(60_000, 5_000 * 2 ** failures));
    timer = window.setTimeout(() => void refresh(), delay);
  }

  function refresh(): Promise<void> {
    if (disposed) return Promise.resolve();
    if (inFlight) return inFlight;
    const requested = getTasks().filter(videoTaskNeedsPolling);
    if (!requested.length) return Promise.resolve();
    window.clearTimeout(timer);
    inFlight = (async () => {
      try {
        const response = await fetchVideoGenerationTasks(requested.map((task) => task.id));
        if (disposed) return;
        const snapshots = new Map(requested.map((task) => [task.id, task]));
        const current = new Map(getTasks().map((task) => [task.id, task]));
        const missing = new Set(response.missing_ids || []);
        const updates: VideoGenerationTask[] = [
          ...response.items,
          ...requested.filter((task) => missing.has(task.id)).map((task): VideoGenerationTask => ({
            ...task, status: "error", cancellation_pending: false, reconciliation_required: false,
            error: "生成任务不存在或已被删除",
          })),
        ];
        // A response started before cancellation or a conversation change must not overwrite newer state.
        applyUpdates(updates.filter((task) => snapshots.has(task.id) && current.get(task.id) === snapshots.get(task.id)));
        failures = 0;
        error.value = "";
      } catch (cause) {
        failures = Math.min(failures + 1, 4);
        error.value = cause instanceof Error ? cause.message : "视频状态同步失败";
      } finally {
        inFlight = null;
        schedule();
      }
    })();
    return inFlight;
  }

  watch(() => getTasks().filter(videoTaskNeedsPolling).map((task) => task.id).join(","), schedule, { immediate: true });
  document.addEventListener("visibilitychange", schedule);
  onBeforeUnmount(() => {
    disposed = true;
    window.clearTimeout(timer);
    document.removeEventListener("visibilitychange", schedule);
  });
  return { refresh, error };
}
