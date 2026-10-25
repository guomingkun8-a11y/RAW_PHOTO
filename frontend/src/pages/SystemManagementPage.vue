<script setup lang="ts">
import { ArrowDown, ArrowUp, GripVertical, RotateCcw, Save, Settings2 } from "@lucide/vue";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { toast } from "vue-sonner";

import AlertSettingsPanel from "@/features/alerts/AlertSettingsPanel.vue";
import AnnouncementManagementPanel from "@/features/system/AnnouncementManagementPanel.vue";
import {
  fetchNavigationLayout, navItems, saveNavigationLayout, type NavigationItem,
} from "@/features/system/navigation";

const initialTab = useRoute().query.tab;
const activeTab = ref<"navigation" | "alerts" | "announcements">(initialTab === "alerts" || initialTab === "announcements" ? initialTab : "navigation");
const draft = ref<NavigationItem[]>(navItems.map(({ id }) => ({ id, visible: true })));
const loading = ref(true);
const saving = ref(false);
const dragIndex = ref<number | null>(null);
const loadError = ref("");
const visibleCount = computed(() => draft.value.filter((item) => item.visible).length);

function definition(id: string) {
  return navItems.find((item) => item.id === id);
}

function move(index: number, target: number) {
  if (target < 0 || target >= draft.value.length || index === target) return;
  const items = [...draft.value];
  const [moved] = items.splice(index, 1);
  if (!moved) return;
  items.splice(target, 0, moved);
  draft.value = items;
}

function drop(target: number) {
  if (dragIndex.value !== null) move(dragIndex.value, target);
  dragIndex.value = null;
}

async function save() {
  saving.value = true;
  try {
    draft.value = (await saveNavigationLayout(draft.value)).map((item) => ({ ...item }));
    toast.success("全员导航已更新");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "保存失败");
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  try {
    draft.value = (await fetchNavigationLayout()).map((item) => ({ ...item }));
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : "读取导航设置失败";
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <main class="mx-auto w-full max-w-[1180px] px-4 pb-20 pt-7 sm:px-6">
    <div class="flex items-center gap-3 border-b border-black/[0.08] pb-5 dark:border-white/10">
      <span class="flex size-10 items-center justify-center rounded-md bg-slate-950 text-white dark:bg-white dark:text-slate-950"><Settings2 class="size-5" /></span>
      <div>
        <h1 class="text-[24px] font-semibold text-slate-950 dark:text-stone-50">系统管理</h1>
        <p class="mt-0.5 text-[13px] text-slate-500 dark:text-stone-400">全员导航、运行告警与更新公告</p>
      </div>
    </div>

    <div class="mt-5 flex gap-1 border-b border-black/[0.08] dark:border-white/10" role="tablist" aria-label="系统管理分类">
      <button type="button" role="tab" :aria-selected="activeTab === 'navigation'" class="border-b-2 px-4 py-3 text-[14px] font-semibold" :class="activeTab === 'navigation' ? 'border-[#315be8] text-[#315be8]' : 'border-transparent text-slate-500 hover:text-slate-900 dark:hover:text-white'" @click="activeTab = 'navigation'">导航布局</button>
      <button type="button" role="tab" :aria-selected="activeTab === 'alerts'" class="border-b-2 px-4 py-3 text-[14px] font-semibold" :class="activeTab === 'alerts' ? 'border-[#315be8] text-[#315be8]' : 'border-transparent text-slate-500 hover:text-slate-900 dark:hover:text-white'" @click="activeTab = 'alerts'">监控告警</button>
      <button type="button" role="tab" :aria-selected="activeTab === 'announcements'" class="border-b-2 px-4 py-3 text-[14px] font-semibold" :class="activeTab === 'announcements' ? 'border-[#315be8] text-[#315be8]' : 'border-transparent text-slate-500 hover:text-slate-900 dark:hover:text-white'" @click="activeTab = 'announcements'">公告管理</button>
    </div>

    <section v-if="activeTab === 'navigation'" class="pt-6" aria-label="导航布局">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 class="text-[18px] font-semibold text-slate-900 dark:text-stone-100">侧栏导航</h2>
          <p class="mt-1 text-[13px] text-slate-500 dark:text-stone-400">调整顺序和显示状态，保存后应用到所有成员。管理员专属菜单仍仅管理员可见。</p>
        </div>
        <div class="flex items-center gap-2">
          <button type="button" class="inline-flex h-10 items-center gap-2 rounded-md border border-black/10 px-3 text-[13px] text-slate-700 dark:border-white/10 dark:text-stone-200" @click="draft = navItems.map(({ id }) => ({ id, visible: true }))"><RotateCcw class="size-4" />恢复默认</button>
          <button type="button" class="inline-flex h-10 items-center gap-2 rounded-md bg-slate-950 px-4 text-[13px] font-semibold text-white disabled:opacity-50 dark:bg-white dark:text-slate-950" :disabled="saving || loading" @click="save"><Save class="size-4" />保存导航</button>
        </div>
      </div>
      <p v-if="loadError" class="mt-4 text-[13px] text-rose-600">{{ loadError }}</p>
      <div class="mt-5 overflow-hidden rounded-md border border-black/[0.08] bg-white dark:border-white/10 dark:bg-[#171a21]">
        <div class="flex justify-between border-b border-black/[0.06] px-4 py-3 text-[12px] font-medium text-slate-500 dark:border-white/10"><span>菜单项 · {{ visibleCount }} 项显示</span><span>拖动或使用箭头排序</span></div>
        <div v-if="loading" class="p-5 text-[13px] text-slate-500">正在读取导航设置…</div>
        <div v-for="(item, index) in loading ? [] : draft" :key="item.id" class="flex min-h-14 items-center gap-2 border-b border-black/[0.06] px-3 last:border-0 dark:border-white/10" :class="{ 'opacity-50': !item.visible, 'bg-[#4F7CFF]/[0.06]': dragIndex === index }" draggable="true" @dragstart="dragIndex = index" @dragend="dragIndex = null" @dragover.prevent @drop.prevent="drop(index)">
          <GripVertical class="size-4 shrink-0 cursor-grab text-slate-400" />
          <component :is="definition(item.id)?.icon" class="size-4 shrink-0 text-slate-600 dark:text-stone-300" />
          <span class="min-w-0 flex-1 truncate text-[13px] font-medium text-slate-900 dark:text-stone-100">{{ definition(item.id)?.label }}</span>
          <span v-if="definition(item.id)?.adminOnly" class="hidden text-[11px] text-slate-500 sm:inline">仅管理员</span>
          <label class="inline-flex items-center gap-2 text-[12px] text-slate-600 dark:text-stone-300"><input v-model="item.visible" type="checkbox" class="size-4 accent-[#315be8]" :aria-label="`显示${definition(item.id)?.label}`" />显示</label>
          <button type="button" class="ml-1 inline-flex size-8 items-center justify-center rounded-md hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-white/10" :disabled="index === 0" :aria-label="`上移${definition(item.id)?.label}`" @click="move(index, index - 1)"><ArrowUp class="size-4" /></button>
          <button type="button" class="inline-flex size-8 items-center justify-center rounded-md hover:bg-slate-100 disabled:opacity-30 dark:hover:bg-white/10" :disabled="index === draft.length - 1" :aria-label="`下移${definition(item.id)?.label}`" @click="move(index, index + 1)"><ArrowDown class="size-4" /></button>
        </div>
      </div>
    </section>

    <div v-else-if="activeTab === 'alerts'" class="pt-6"><AlertSettingsPanel /></div>
    <div v-else class="pt-6"><AnnouncementManagementPanel /></div>
  </main>
</template>
