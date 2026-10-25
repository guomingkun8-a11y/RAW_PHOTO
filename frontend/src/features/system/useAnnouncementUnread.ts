import { computed, ref, watch, type Ref } from "vue";

import type { SystemAnnouncement } from "@/lib/api";
import { storageKey } from "@/lib/storage-namespace";
import { sessionState } from "@/stores/session";

export function useAnnouncementUnread(items: Ref<SystemAnnouncement[]>, open: Ref<boolean>) {
  const readVersions = ref<Record<string, string>>({});
  const key = computed(() => storageKey(`gmkraw:announcement-read:${sessionState.session?.subjectId || "anonymous"}`));
  const version = (item: SystemAnnouncement) => item.updated_at || item.created_at;
  const count = computed(() => items.value.filter(
    (item) => item.enabled && readVersions.value[item.id] !== version(item),
  ).length);

  watch(key, (value) => {
    try {
      const stored = JSON.parse(localStorage.getItem(value) || "{}");
      readVersions.value = stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
    } catch {
      readVersions.value = {};
    }
  }, { immediate: true });

  watch([items, open], () => {
    if (!open.value || !items.value.length) return;
    readVersions.value = Object.fromEntries(items.value.map((item) => [item.id, version(item)]));
    try {
      localStorage.setItem(key.value, JSON.stringify(readVersions.value));
    } catch {
      // Keep read state for this session when browser storage is unavailable.
    }
  });

  return count;
}
