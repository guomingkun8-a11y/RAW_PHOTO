import { onBeforeUnmount, onMounted } from "vue";

type RefreshCallback = () => void | Promise<void>;

export function useVisibilityAwareInterval(callback: RefreshCallback, intervalMs: number) {
  let enabled = false;
  let running = false;
  let timer = 0;

  function clearTimer() {
    window.clearInterval(timer);
    timer = 0;
  }

  async function run() {
    if (!enabled || running || document.visibilityState !== "visible") return;
    running = true;
    try {
      await callback();
    } finally {
      running = false;
    }
  }

  function schedule() {
    clearTimer();
    if (!enabled || document.visibilityState !== "visible") return;
    timer = window.setInterval(() => void run(), intervalMs);
  }

  function restart(nextEnabled = true) {
    enabled = nextEnabled;
    schedule();
  }

  function handleVisibilityChange() {
    schedule();
    if (enabled && document.visibilityState === "visible") void run();
  }

  onMounted(() => document.addEventListener("visibilitychange", handleVisibilityChange));
  onBeforeUnmount(() => {
    enabled = false;
    clearTimer();
    document.removeEventListener("visibilitychange", handleVisibilityChange);
  });

  return { restart };
}
